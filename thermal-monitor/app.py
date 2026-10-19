"""Thermal Core HUD – app đo nhiệt độ CPU / GPU / Mainboard chạy local.

Chạy:  python app.py            (tự mở giao diện)
       python app.py --demo     (dữ liệu mô phỏng để xem giao diện)
       python app.py --help
Bản đóng gói: ThermalCoreHUD.exe (cùng tham số).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import sensors

APP_NAME = "ThermalCoreHUD"
APP_VERSION = "1.1.0"
DEFAULT_LHM_URL = "http://127.0.0.1:8085/data.json"
IS_WINDOWS = sys.platform.startswith("win")
FROZEN = getattr(sys, "frozen", False)

if IS_WINDOWS:
    import winsupport
else:
    winsupport = None


def resource_dir() -> str:
    # Khi đóng gói bằng PyInstaller, file tĩnh nằm trong sys._MEIPASS
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "web")


class Sampler:
    """Đọc cảm biến theo chu kỳ trong một luồng riêng, phát snapshot mới cho các client SSE."""

    def __init__(self, provider, interval: float, status_fn=None):
        self.provider = provider
        self.interval = interval
        self.status_fn = status_fn  # trả về thông báo trạng thái (vd. đang tải LHM) hoặc None
        self.cond = threading.Condition()
        self.seq = 0
        self.latest: dict | None = None
        self.clients = 0
        self.had_client = False
        self.idle_since = time.monotonic()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                snap = sensors.snapshot(self.provider.read())
            except Exception as exc:  # noqa: BLE001 - lỗi đọc không được làm dừng vòng lặp
                snap = {"ts": time.time(), "source": "—", "mode": "error", "sensors": [],
                        "primary": {}, "messages": [f"Lỗi đọc cảm biến: {exc}"]}
            status = self.status_fn() if self.status_fn else None
            if status:
                # Trong lúc đang chuẩn bị LHM, lỗi "không kết nối được LHM" là điều đương nhiên -> ẩn đi
                snap["messages"] = [status] + [m for m in snap["messages"] if "LibreHardwareMonitor" not in m]
            snap["interval"] = self.interval
            snap["readMs"] = round((time.monotonic() - started) * 1000)
            snap["version"] = APP_VERSION
            with self.cond:
                self.seq += 1
                self.latest = snap
                self.cond.notify_all()
            time.sleep(max(0.05, self.interval - (time.monotonic() - started)))

    def wait_next(self, last_seq: int, timeout: float) -> tuple[int, dict | None]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq, timeout=timeout)
            return self.seq, self.latest

    def client_delta(self, delta: int) -> None:
        with self.cond:
            self.clients += delta
            self.had_client = True
            if self.clients == 0:
                self.idle_since = time.monotonic()


def make_handler(sampler: Sampler, web_dir: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"{APP_NAME}/{APP_VERSION}"

        def log_message(self, fmt, *args):  # giữ console sạch
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/info":
                return self._send(200, json.dumps({"app": APP_NAME, "version": APP_VERSION}).encode(),
                                  "application/json")
            if path == "/api/snapshot":
                _, snap = sampler.wait_next(-1, 0)
                return self._send(200, json.dumps(snap or {}).encode(), "application/json")
            if path == "/api/stream":
                return self._stream()
            return self._static(path)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            seq = -1
            sampler.client_delta(+1)
            try:
                self.wfile.write(b"retry: 2000\n\n")
                while True:
                    new_seq, snap = sampler.wait_next(seq, timeout=15)
                    if new_seq == seq or snap is None:
                        self.wfile.write(b": keep-alive\n\n")  # giữ kết nối khi không có dữ liệu mới
                    else:
                        seq = new_seq
                        self.wfile.write(b"data: " + json.dumps(snap).encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return
            finally:
                sampler.client_delta(-1)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            full = os.path.realpath(os.path.join(web_dir, rel))
            if not full.startswith(os.path.realpath(web_dir) + os.sep) or not os.path.isfile(full):
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

    return Handler


def _browser_profile() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "browser")


def open_ui(url: str, mode: str) -> None:
    if mode == "none":
        return
    if mode in ("auto", "window"):
        # Cửa sổ app riêng bằng Edge/Chrome chế độ --app, hồ sơ trình duyệt riêng (không đụng tab đang mở)
        flags = [f"--app={url}", f"--user-data-dir={_browser_profile()}", "--no-first-run",
                 "--no-default-browser-check", "--window-size=1280,800"]
        if IS_WINDOWS:
            exe = winsupport.find_browser()
            if exe:
                if winsupport.is_admin():
                    # App chạy quyền Admin nhưng trình duyệt nên chạy quyền thường
                    if winsupport.open_unelevated(exe, " ".join(f'"{f}"' for f in flags)):
                        return
                try:
                    subprocess.Popen([exe, *flags])
                    return
                except OSError:
                    pass
        else:
            for c in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
                exe = shutil.which(c)
                if exe:
                    try:
                        subprocess.Popen([exe, *flags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except OSError:
                        continue
    webbrowser.open(url)


def existing_instance(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=1) as r:
            return json.loads(r.read()).get("app") == APP_NAME
    except (OSError, ValueError):
        return False


def setup_logging() -> None:
    """Bản .exe không có console -> ghi log ra %LOCALAPPDATA%\\ThermalCoreHUD\\thermal-core.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = os.path.join(winsupport.data_dir() if winsupport else os.path.expanduser("~"), "thermal-core.log")
    log = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - mở suốt vòng đời app
    sys.stdout = sys.stderr = log
    print(f"\n=== {APP_NAME} {APP_VERSION} · {time.strftime('%Y-%m-%d %H:%M:%S')} ===")


def fatal(msg: str) -> None:
    print("LỖI:", msg, file=sys.stderr)
    if IS_WINDOWS and FROZEN:
        winsupport.message_box(msg)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Thermal Core HUD – theo dõi nhiệt độ CPU/GPU/Mainboard realtime")
    ap.add_argument("--port", type=int, default=8787, help="cổng giao diện (mặc định 8787)")
    ap.add_argument("--interval", type=float, default=1.0, help="chu kỳ đọc cảm biến, giây (mặc định 1.0)")
    ap.add_argument("--demo", action="store_true", help="dùng dữ liệu mô phỏng")
    ap.add_argument("--lhm-url", default=None,
                    help=f"địa chỉ data.json của LibreHardwareMonitor (mặc định trên Windows: {DEFAULT_LHM_URL})")
    ap.add_argument("--lhm-auth", default=None, help="user:password nếu LHM web server có đặt mật khẩu")
    ap.add_argument("--no-manage-lhm", action="store_true",
                    help="không tự tải/khởi động LibreHardwareMonitor (tự quản lý LHM)")
    ap.add_argument("--ui", choices=["auto", "window", "browser", "none"], default="auto",
                    help="cách mở giao diện: cửa sổ app (auto/window), tab trình duyệt, hoặc không mở")
    ap.add_argument("--keep-running", action="store_true",
                    help="không tự thoát khi đóng cửa sổ giao diện (mặc định bản .exe tự thoát)")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = ap.parse_args()

    setup_logging()
    url = f"http://127.0.0.1:{args.port}/"
    if existing_instance(args.port):
        print("App đã chạy sẵn -> chỉ mở lại giao diện.")
        open_ui(url, args.ui)
        return

    interval = min(10.0, max(0.25, args.interval))
    manager = None
    if args.demo:
        provider = sensors.DemoAuto()
    else:
        lhm_url = args.lhm_url or (DEFAULT_LHM_URL if IS_WINDOWS else None)
        provider = sensors.AutoProvider(lhm_url, args.lhm_auth)
        if IS_WINDOWS and not args.no_manage_lhm and not args.lhm_url:
            manager = winsupport.LhmManager()
            threading.Thread(target=manager.ensure_running, daemon=True).start()
            if not winsupport.is_admin():
                print("Cảnh báo: app không chạy quyền Administrator – có thể thiếu nhiệt độ CPU/mainboard.")

    sampler = Sampler(provider, interval, status_fn=(lambda: manager.status) if manager else None)
    sampler.start()

    # Chỉ lắng nghe 127.0.0.1: máy khác trong mạng không truy cập được.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(sampler, resource_dir()))
    except OSError as exc:
        fatal(f"Không mở được cổng {args.port} ({exc}). Hãy đóng ứng dụng đang dùng cổng này "
              f"hoặc chạy với tham số --port khác.")
    server.daemon_threads = True
    print(f"{APP_NAME} {APP_VERSION} đang chạy tại {url}  (Ctrl+C để thoát)")
    first = sampler.wait_next(0, timeout=10)[1] or {}
    print(f"Nguồn dữ liệu: {first.get('source', '—')} | {len(first.get('sensors', []))} cảm biến nhiệt độ")
    for msg in first.get("messages", []):
        print("  !", msg)
    open_ui(url, args.ui)

    # Bản .exe: đóng cửa sổ giao diện -> app tự thoát (không để tiến trình chạy ngầm)
    if FROZEN and not args.keep_running and args.ui != "none":
        def watchdog() -> None:
            while True:
                time.sleep(2)
                with sampler.cond:
                    idle = sampler.had_client and sampler.clients == 0 and time.monotonic() - sampler.idle_since > 10
                if idle:
                    print("Cửa sổ giao diện đã đóng -> thoát.")
                    server.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    finally:
        server.server_close()
        if manager:
            manager.shutdown()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - hiện lỗi cho người dùng bản .exe
        import traceback
        traceback.print_exc()
        fatal(f"Lỗi không mong muốn: {exc}")
