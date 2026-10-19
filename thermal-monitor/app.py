"""Thermal Core HUD – app đo nhiệt độ CPU / GPU / Mainboard chạy local.

Chạy:  python app.py            (tự mở giao diện)
       python app.py --demo     (dữ liệu mô phỏng để xem giao diện)
       python app.py --help
Bản đóng gói: ThermalCoreHUD.exe (cùng tham số).
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import shutil
import signal
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
APP_VERSION = "1.2.0"
DEFAULT_LHM_URL = "http://127.0.0.1:8085/data.json"
IS_WINDOWS = sys.platform.startswith("win")
FROZEN = getattr(sys, "frozen", False)

if IS_WINDOWS:
    import winsupport
else:
    winsupport = None

# Windows: khi đầu ra bị chuyển hướng (pipe/file) Python dùng bảng mã ANSI (cp1252) và lỗi khi in tiếng Việt
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


DEFAULT_SETTINGS = {
    "unit": "C",
    # Ngưỡng cho ThinkPad X1 Yoga Gen 5 (i7-10610U, TjMax 100 °C; iGPU chung đế với CPU)
    "thresholds": {"cpu": {"warn": 85, "crit": 95}, "gpu": {"warn": 85, "crit": 95},
                   "board": {"warn": 65, "crit": 80}},
    "picks": {},
    "overlay": {"enabled": False, "opacity": 0.85, "x": None, "y": None},
}
GAUGE_KEYS = ("cpu", "gpu", "board")


def settings_path() -> str:
    if IS_WINDOWS:
        base = winsupport.data_dir()
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "thermalcorehud")
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, "settings.json")


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def validate_settings_patch(patch) -> dict:
    """Chỉ nhận các khoá đã biết, kiểu và khoảng giá trị hợp lệ (dữ liệu đến từ trình duyệt)."""
    if not isinstance(patch, dict):
        raise ValueError("settings phải là object")
    out: dict = {}
    if "unit" in patch:
        if patch["unit"] not in ("C", "F"):
            raise ValueError("unit phải là C hoặc F")
        out["unit"] = patch["unit"]
    if "thresholds" in patch:
        th = patch["thresholds"]
        if not isinstance(th, dict):
            raise ValueError("thresholds không hợp lệ")
        out["thresholds"] = {}
        for k, v in th.items():
            if k not in GAUGE_KEYS or not isinstance(v, dict):
                raise ValueError(f"thresholds.{k} không hợp lệ")
            warn, crit = v.get("warn"), v.get("crit")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 30 <= x <= 125 for x in (warn, crit)) \
                    or warn >= crit:
                raise ValueError(f"thresholds.{k}: cần 30 ≤ warn < crit ≤ 125")
            out["thresholds"][k] = {"warn": warn, "crit": crit}
    if "picks" in patch:
        pk = patch["picks"]
        if not isinstance(pk, dict) or any(k not in GAUGE_KEYS or not isinstance(v, str) or len(v) > 300
                                           for k, v in pk.items()):
            raise ValueError("picks không hợp lệ")
        out["picks"] = dict(pk)
    if "overlay" in patch:
        ov = patch["overlay"]
        if not isinstance(ov, dict):
            raise ValueError("overlay không hợp lệ")
        out["overlay"] = {}
        if "opacity" in ov:
            if not isinstance(ov["opacity"], (int, float)) or not 0.25 <= ov["opacity"] <= 1:
                raise ValueError("overlay.opacity phải trong 0.25–1")
            out["overlay"]["opacity"] = float(ov["opacity"])
    return out


class SettingsStore:
    """Cài đặt dùng chung cho giao diện web và overlay, lưu ra file JSON."""

    def __init__(self, path: str | None):
        self.path = path
        self.lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    _deep_merge(self.data, {k: v for k, v in loaded.items() if k in DEFAULT_SETTINGS})
            except (OSError, ValueError) as exc:
                print("Không đọc được settings.json, dùng mặc định:", exc)
        self.listeners: list = []

    def get(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, patch: dict) -> dict:
        with self.lock:
            if "picks" in patch:  # picks thay thế toàn bộ (cho phép quay về AUTO)
                self.data["picks"] = {}
            _deep_merge(self.data, copy.deepcopy(patch))
            snapshot = copy.deepcopy(self.data)
            if self.path:
                try:
                    tmp = self.path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(snapshot, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, self.path)
                except OSError as exc:
                    print("Không lưu được settings.json:", exc)
        for fn in self.listeners:
            fn(patch)
        return snapshot


def resource_dir() -> str:
    # Khi đóng gói bằng PyInstaller, file tĩnh nằm trong sys._MEIPASS
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "web")


class Sampler:
    """Đọc cảm biến theo chu kỳ trong một luồng riêng, phát snapshot mới cho các client SSE."""

    def __init__(self, provider, interval: float, status_fn=None, extra_fn=None):
        self.provider = provider
        self.extra_fn = extra_fn  # trường bổ sung cho snapshot (vd. trạng thái overlay)
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
            if self.extra_fn:
                snap.update(self.extra_fn())
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


def make_handler(sampler: Sampler, web_dir: str, store: SettingsStore, overlay_ctl=None):
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
            if path == "/api/settings":
                return self._json(200, store.get())
            return self._static(path)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_POST(self):  # noqa: N802
            # Header tuỳ biến buộc trình duyệt kiểm tra CORS -> trang web lạ không gọi được API này
            if self.headers.get("X-TCH") != "1":
                return self._json(403, {"error": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 65536:
                    raise ValueError("body quá lớn")
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            path = urlparse(self.path).path
            if path == "/api/settings":
                try:
                    patch = validate_settings_patch(body)
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                return self._json(200, store.update(patch))
            if path == "/api/overlay":
                action = body.get("action") if isinstance(body, dict) else None
                if action not in ("show", "hide", "toggle"):
                    return self._json(400, {"error": "action phải là show/hide/toggle"})
                if overlay_ctl is None:
                    return self._json(409, {"error": "overlay không khả dụng trên máy này"})
                overlay_ctl.request(action)
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

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


def post_local(port: int, path: str, body: dict) -> bool:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-TCH": "1"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2):
            return True
    except OSError:
        return False


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
    ap.add_argument("--overlay", action="store_true",
                    help="chỉ hiện overlay 3 đồng hồ ở góc trên bên phải (không mở cửa sổ chính)")
    ap.add_argument("--keep-running", action="store_true",
                    help="không tự thoát khi đóng cửa sổ giao diện (mặc định bản .exe tự thoát)")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = ap.parse_args()

    setup_logging()
    url = f"http://127.0.0.1:{args.port}/"
    if existing_instance(args.port):
        print("App đã chạy sẵn -> chuyển yêu cầu cho bản đang chạy.")
        if args.overlay:
            post_local(args.port, "/api/overlay", {"action": "show"})
        else:
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

    store = SettingsStore(settings_path())
    stop = threading.Event()

    # Overlay cần cửa sổ đồ hoạ (Tk) chạy trên luồng chính; máy không có màn hình thì bỏ qua.
    root = ctl = None
    try:
        import overlay
        root = overlay.create_root()
    except ImportError as exc:
        print("Overlay không khả dụng (thiếu Pillow?):", exc)

    def request_quit() -> None:
        stop.set()
        if ctl is not None:
            ctl.request("quit")

    if root is not None:
        def open_main() -> None:
            threading.Thread(target=open_ui, args=(url, "window"), daemon=True).start()

        ctl = overlay.OverlayController(root, lambda: sampler.latest, store, open_main, request_quit)
        store.listeners.append(lambda patch: ctl.request("reload"))
    else:
        print("Overlay tắt: không có màn hình đồ hoạ hoặc thiếu tkinter/Pillow.")

    sampler = Sampler(provider, interval, status_fn=(lambda: manager.status) if manager else None,
                      extra_fn=lambda: {"overlay": None if ctl is None else ctl.visible})
    sampler.start()

    # Chỉ lắng nghe 127.0.0.1: máy khác trong mạng không truy cập được.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(sampler, resource_dir(), store, ctl))
    except OSError as exc:
        fatal(f"Không mở được cổng {args.port} ({exc}). Hãy đóng ứng dụng đang dùng cổng này "
              f"hoặc chạy với tham số --port khác.")
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"{APP_NAME} {APP_VERSION} đang chạy tại {url}  (Ctrl+C để thoát)")
    first = sampler.wait_next(0, timeout=10)[1] or {}
    print(f"Nguồn dữ liệu: {first.get('source', '—')} | {len(first.get('sensors', []))} cảm biến nhiệt độ")
    for msg in first.get("messages", []):
        print("  !", msg)

    overlay_on = ctl is not None and (args.overlay or store.get()["overlay"]["enabled"])
    if overlay_on:
        ctl.request("show")
    if not args.overlay:
        open_ui(url, args.ui)

    # Bản .exe: khi cửa sổ chính đã đóng VÀ overlay đang ẩn quá 10 giây -> tự thoát (không chạy ngầm)
    if FROZEN and not args.keep_running and (args.ui != "none" or overlay_on):
        def watchdog() -> None:
            idle_from = None
            while not stop.is_set():
                time.sleep(1)
                with sampler.cond:
                    used = sampler.had_client or (ctl is not None and ctl.ever_shown)
                    idle = sampler.clients == 0 and not (ctl is not None and ctl.visible)
                idle_from = (idle_from or time.monotonic()) if (used and idle) else None
                if idle_from and time.monotonic() - idle_from > 10:
                    print("Đã đóng cửa sổ chính và overlay -> thoát.")
                    request_quit()
        threading.Thread(target=watchdog, daemon=True).start()

    signal.signal(signal.SIGINT, lambda *_: request_quit())
    try:
        if root is not None:
            root.mainloop()  # thoát khi OverlayController nhận lệnh "quit"
        else:
            while not stop.wait(0.5):
                pass
    finally:
        print("Đã dừng.")
        server.shutdown()
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
