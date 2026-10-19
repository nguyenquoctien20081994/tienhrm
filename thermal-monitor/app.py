"""Thermal Core HUD – app đo nhiệt độ CPU / GPU / Mainboard chạy local.

Chạy:  python app.py            (tự mở giao diện)
       python app.py --demo     (dữ liệu mô phỏng để xem giao diện)
       python app.py --help
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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import sensors

DEFAULT_LHM_URL = "http://localhost:8085/data.json"


def resource_dir() -> str:
    # Khi đóng gói bằng PyInstaller, file tĩnh nằm trong sys._MEIPASS
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "web")


class Sampler:
    """Đọc cảm biến theo chu kỳ trong một luồng riêng, phát snapshot mới cho các client SSE."""

    def __init__(self, provider, interval: float):
        self.provider = provider
        self.interval = interval
        self.cond = threading.Condition()
        self.seq = 0
        self.latest: dict | None = None

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
            snap["interval"] = self.interval
            snap["readMs"] = round((time.monotonic() - started) * 1000)
            with self.cond:
                self.seq += 1
                self.latest = snap
                self.cond.notify_all()
            time.sleep(max(0.05, self.interval - (time.monotonic() - started)))

    def wait_next(self, last_seq: int, timeout: float) -> tuple[int, dict | None]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq, timeout=timeout)
            return self.seq, self.latest


def make_handler(sampler: Sampler, web_dir: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ThermalCoreHUD/1.0"

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


def open_ui(url: str, mode: str) -> None:
    if mode == "none":
        return
    if mode in ("auto", "window"):
        # 1) Cửa sổ app riêng bằng Edge/Chrome chế độ --app (có sẵn trên Windows 10/11)
        candidates = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
        if sys.platform.startswith("win"):
            pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
            candidates = [os.path.join(p, sub) for p in pf if p for sub in (
                r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")] + candidates
        for c in candidates:
            exe = c if os.path.isfile(c) else shutil.which(c)
            if exe:
                # Hồ sơ trình duyệt riêng: cửa sổ app sạch, không đụng tab/profile đang mở
                # (cần thiết khi app chạy quyền Administrator).
                profile = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ThermalCoreHUD", "browser")
                try:
                    subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                                      "--no-default-browser-check", "--window-size=1280,800"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue
    webbrowser.open(url)


def main() -> None:
    ap = argparse.ArgumentParser(description="Thermal Core HUD – theo dõi nhiệt độ CPU/GPU/Mainboard realtime")
    ap.add_argument("--port", type=int, default=8787, help="cổng giao diện (mặc định 8787)")
    ap.add_argument("--interval", type=float, default=1.0, help="chu kỳ đọc cảm biến, giây (mặc định 1.0)")
    ap.add_argument("--demo", action="store_true", help="dùng dữ liệu mô phỏng")
    ap.add_argument("--lhm-url", default=None,
                    help=f"địa chỉ data.json của LibreHardwareMonitor (mặc định trên Windows: {DEFAULT_LHM_URL})")
    ap.add_argument("--lhm-auth", default=None, help="user:password nếu LHM web server có đặt mật khẩu")
    ap.add_argument("--ui", choices=["auto", "window", "browser", "none"], default="auto",
                    help="cách mở giao diện: cửa sổ app (auto/window), tab trình duyệt, hoặc không mở")
    args = ap.parse_args()

    interval = min(10.0, max(0.25, args.interval))
    if args.demo:
        provider = sensors.DemoAuto()
    else:
        lhm_url = args.lhm_url or (DEFAULT_LHM_URL if sys.platform.startswith("win") else None)
        provider = sensors.AutoProvider(lhm_url, args.lhm_auth)

    sampler = Sampler(provider, interval)
    sampler.start()

    # Chỉ lắng nghe 127.0.0.1: máy khác trong mạng không truy cập được.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(sampler, resource_dir()))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Thermal Core HUD đang chạy tại {url}  (Ctrl+C để thoát)")
    first = sampler.wait_next(0, timeout=10)[1] or {}
    print(f"Nguồn dữ liệu: {first.get('source', '—')} | {len(first.get('sensors', []))} cảm biến nhiệt độ")
    for msg in first.get("messages", []):
        print("  !", msg)
    open_ui(url, args.ui)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")


if __name__ == "__main__":
    main()
