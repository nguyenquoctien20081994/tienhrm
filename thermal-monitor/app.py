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
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import assess
import history
import sensors

APP_NAME = "ThermalCoreHUD"
APP_VERSION = "1.9.1"
APP_AUTHOR = "Nguyễn Quốc Tiến"
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


# Kiểu file tĩnh cố định: trên Windows, mimetypes đọc registry và có máy gán .js = text/plain
# -> trình duyệt chặn script vì header nosniff. Không phụ thuộc registry nữa.
STATIC_TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".json": "application/json",
                ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
                ".woff2": "font/woff2"}

INTERVAL_CHOICES = (1, 5, 6, 10, 15, 30, 60)
SETTINGS_VERSION = 3  # 2: chu kỳ mặc định 15 s -> 6 s; 3: overlay trong suốt thêm 30%
HISTORY_DAY_CHOICES = (7, 14, 30)
UI_STYLES = ("hud", "cockpit")
BOARD_SOURCES = ("auto", "hwinfo")  # A: LHM (thiếu thì dùng ổ SSD) / B: HWiNFO
UI_THEMES = ("auto", "night", "day")
UI_BRANDS = ("thermal", "viettam")  # giao diện gốc / giao diện Công ty TNHH Thiết Bị Việt Tâm
BRAND_SLOTS = ("nam", "nu", "logo")  # ảnh nhân vật nam, nữ và logo cho giao diện Việt Tâm
BRAND_MAX_BYTES = 3 * 1024 * 1024
# Chỉ nhận ảnh bitmap; không nhận SVG vì SVG có thể chứa script chạy trong trang
_IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
                (b"GIF89a", "image/gif"))
DEFAULT_SETTINGS = {
    "unit": "C",
    "settings_version": SETTINGS_VERSION,
    "interval": 6,  # giây giữa 2 lần đọc cảm biến
    "history_days": 30,  # số ngày giữ nhật ký nhiệt độ
    "board_source": "auto",  # nguồn nhiệt độ mainboard: "auto" (A) hoặc "hwinfo" (B)
    # đồng hồ cơ / buồng lái; ngày / đêm / tự động theo giờ; giao diện gốc / Việt Tâm
    "ui": {"style": "hud", "theme": "auto", "brand": "thermal"},
    # Ngưỡng cho ThinkPad X1 Yoga Gen 5 (i7-10610U, TjMax 100 °C; iGPU chung đế với CPU)
    "thresholds": {"cpu": {"warn": 85, "crit": 95}, "gpu": {"warn": 85, "crit": 95},
                   "board": {"warn": 65, "crit": 80}},
    "picks": {},
    "overlay": {"enabled": False, "opacity": 0.55, "x": None, "y": None},
}
GAUGE_KEYS = ("cpu", "gpu", "board")


def settings_path() -> str:
    if IS_WINDOWS:
        base = winsupport.data_dir()
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "thermalcorehud")
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, "settings.json")


def image_type(data: bytes) -> str | None:
    """Loại ảnh theo vài byte đầu (không tin phần mở rộng hay Content-Type do trình duyệt gửi)."""
    for magic, ctype in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class BrandStore:
    """Ảnh nhân vật / logo người dùng tải lên cho giao diện Việt Tâm, lưu cạnh settings.json."""

    def __init__(self, folder: str):
        self.folder = folder

    def _path(self, slot: str) -> str:
        return os.path.join(self.folder, slot + ".img")

    def available(self) -> dict:
        return {slot: os.path.isfile(self._path(slot)) for slot in BRAND_SLOTS}

    def load(self, slot: str) -> tuple[bytes, str] | None:
        try:
            with open(self._path(slot), "rb") as f:
                data = f.read(BRAND_MAX_BYTES + 1)
        except OSError:
            return None
        ctype = image_type(data)
        return (data, ctype) if ctype else None

    def save(self, slot: str, data: bytes) -> None:
        if len(data) > BRAND_MAX_BYTES:
            raise ValueError("Ảnh quá lớn (tối đa 3 MB).")
        if image_type(data) is None:
            raise ValueError("Chỉ nhận ảnh PNG, JPG, GIF hoặc WebP.")
        os.makedirs(self.folder, exist_ok=True)
        tmp = self._path(slot) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, self._path(slot))

    def delete(self, slot: str) -> None:
        try:
            os.remove(self._path(slot))
        except FileNotFoundError:
            pass


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
    if "interval" in patch:
        if isinstance(patch["interval"], bool) or patch["interval"] not in INTERVAL_CHOICES:  # True == 1 trong Python
            raise ValueError(f"interval phải là một trong {INTERVAL_CHOICES}")
        out["interval"] = patch["interval"]
    if "board_source" in patch:
        if patch["board_source"] not in BOARD_SOURCES:
            raise ValueError(f"board_source phải là một trong {BOARD_SOURCES}")
        out["board_source"] = patch["board_source"]
    if "history_days" in patch:
        if patch["history_days"] not in HISTORY_DAY_CHOICES:
            raise ValueError(f"history_days phải là một trong {HISTORY_DAY_CHOICES}")
        out["history_days"] = patch["history_days"]
    if "ui" in patch:
        ui = patch["ui"]
        if not isinstance(ui, dict) or ui.get("style", "hud") not in UI_STYLES or ui.get("theme", "auto") not in UI_THEMES \
                or ui.get("brand", "thermal") not in UI_BRANDS:
            raise ValueError(f"ui.style phải thuộc {UI_STYLES}, ui.theme thuộc {UI_THEMES}, ui.brand thuộc {UI_BRANDS}")
        out["ui"] = {k: ui[k] for k in ("style", "theme", "brand") if k in ui}
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
                    # Bản ≤ 1.4 lưu sẵn chu kỳ mặc định cũ (15 s) -> chuyển sang mặc định mới 6 s
                    version = loaded.get("settings_version", 1)
                    if version < 2 and loaded.get("interval") == 15:
                        self.data["interval"] = 6
                    saved_opacity = (loaded.get("overlay") or {}).get("opacity")
                    if version < 3 and isinstance(saved_opacity, (int, float)):
                        # Overlay trong suốt thêm 30% so với độ đang dùng (tối thiểu 25% độ đậm)
                        self.data["overlay"]["opacity"] = max(0.25, round(saved_opacity - 0.30, 2))
                    self.data["settings_version"] = SETTINGS_VERSION
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
            elif patch.get("board_source", self.data["board_source"]) != self.data["board_source"]:
                # Đổi nguồn A/B: bỏ cảm biến mainboard chọn tay, nếu không đồng hồ vẫn bám cảm biến cũ
                self.data["picks"].pop("board", None)
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

    HISTORY_SEC = 600  # lịch sử ngắn để tính tốc độ tăng nhiệt cho phần nhận định

    def __init__(self, provider, interval_fn, settings_fn, status_fn=None, extra_fn=None, db=None):
        self.provider = provider
        self.db = db  # history.HistoryDB hoặc None
        self.peak_clock = db.peak_clock() if db else None
        self.interval_fn = interval_fn  # đọc lại mỗi vòng -> đổi chu kỳ trong CALIBRATE có hiệu lực ngay
        self.settings_fn = settings_fn
        self.status_fn = status_fn  # trả về thông báo trạng thái (vd. đang tải LHM) hoặc None
        self.extra_fn = extra_fn  # trường bổ sung cho snapshot (vd. trạng thái overlay)
        self.cond = threading.Condition()
        self.wake = threading.Event()
        self.seq = 0
        self.latest: dict | None = None
        self.clients = 0
        self.had_client = False
        self.idle_since = time.monotonic()
        self.history: dict[str, list] = {}

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _remember(self, snap: dict) -> None:
        now = snap["ts"]
        for s in snap.get("sensors", []):
            if s.get("value") is None:
                continue
            h = self.history.setdefault(s["id"], [])
            h.append((now, s["value"]))
            while h and now - h[0][0] > self.HISTORY_SEC:
                h.pop(0)

    def _loop(self) -> None:
        while True:
            started = time.monotonic()
            interval = self.interval_fn()
            try:
                snap = sensors.snapshot(self.provider.read())
            except Exception as exc:  # noqa: BLE001 - lỗi đọc không được làm dừng vòng lặp
                snap = {"ts": time.time(), "source": "—", "mode": "error", "sensors": [], "metrics": {},
                        "primary": {}, "messages": [f"Lỗi đọc cảm biến: {exc}"]}
            status = self.status_fn() if self.status_fn else None
            if status:
                # Trong lúc đang chuẩn bị LHM, lỗi "không kết nối được LHM" là điều đương nhiên -> ẩn đi
                snap["messages"] = [status] + [m for m in snap["messages"] if "LibreHardwareMonitor" not in m]
            self._remember(snap)
            settings = self.settings_fn()
            m = snap.get("metrics") or {}
            clock = m.get("cpu_clock_max") or m.get("cpu_clock")
            if (m.get("cpu_load") or 0) >= 50 and clock:
                self.peak_clock = max(self.peak_clock or 0, clock)
            try:
                snap["assessment"] = assess.evaluate(snap, settings, self.history, self.peak_clock)
            except Exception as exc:  # noqa: BLE001 - nhận định lỗi không được làm mất số liệu
                snap["assessment"] = None
                print("Lỗi nhận định:", exc)
            if self.db and snap.get("mode") in ("live", "demo"):
                try:
                    a = snap["assessment"] or {}
                    self.db.record(snap["ts"], {**assess.chosen_values(snap, settings),
                                                "cpu_load": m.get("cpu_load"), "cpu_clock": clock,
                                                "throttle": (a.get("throttle") or {}).get("state"),
                                                "level": a.get("level")}, settings["history_days"])
                except Exception as exc:  # noqa: BLE001 - lỗi ghi nhật ký không được làm dừng việc đo
                    print("Lỗi ghi nhật ký:", exc)
            snap["interval"] = interval
            snap["readMs"] = round((time.monotonic() - started) * 1000)
            snap["version"] = APP_VERSION
            if self.extra_fn:
                snap.update(self.extra_fn())
            with self.cond:
                self.seq += 1
                self.latest = snap
                self.cond.notify_all()
            self.wake.wait(max(0.05, interval - (time.monotonic() - started)))
            self.wake.clear()

    def republish(self) -> None:
        """Gửi lại snapshot gần nhất với trạng thái mới (vd. overlay vừa bật/tắt) mà không chờ chu kỳ đo."""
        with self.cond:
            if self.latest is None:
                return
            snap = dict(self.latest)
            if self.extra_fn:
                snap.update(self.extra_fn())
            self.latest = snap
            self.seq += 1
            self.cond.notify_all()

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


def make_handler(sampler: Sampler, web_dir: str, store: SettingsStore, overlay_ctl=None, db=None, diag_fn=None,
                 brand: BrandStore | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"{APP_NAME}/{APP_VERSION}"

        def log_message(self, fmt, *args):  # giữ console sạch
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/info":
                return self._json(200, {"app": APP_NAME, "version": APP_VERSION, "author": APP_AUTHOR,
                                        "host": socket.gethostname()})
            if path == "/api/snapshot":
                _, snap = sampler.wait_next(-1, 0)
                return self._send(200, json.dumps(snap or {}).encode(), "application/json")
            if path == "/api/stream":
                return self._stream()
            if path == "/api/settings":
                return self._json(200, store.get())
            if path == "/api/brand":
                return self._json(200, brand.available() if brand else {})
            if path.startswith("/api/brand/"):
                got = brand.load(path.rsplit("/", 1)[1]) if brand and path.rsplit("/", 1)[1] in BRAND_SLOTS else None
                return self._send(200, *got) if got else self._send(404, b"Not found", "text/plain; charset=utf-8")
            if path == "/api/diagnostics":
                body = json.dumps(diag_fn() if diag_fn else {}, ensure_ascii=False, indent=1, default=str).encode()
                name = time.strftime("thermalcore-chan-doan-%Y%m%d-%H%M.json")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/api/history", "/api/report"):
                if db is None:
                    return self._json(409, {"error": "nhật ký không khả dụng"})
                q = parse_qs(urlparse(self.path).query)
                if path == "/api/history":
                    rng = q.get("range", ["24h"])[0]
                    return self._json(200, db.series(rng if rng in history.RANGES else "24h"))
                days = int(q.get("days", ["7"])[0]) if q.get("days", ["7"])[0].isdigit() else 7
                return self._json(200, db.report(min(max(days, 1), 30), store.get()["thresholds"]))
            return self._static(path)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_POST(self):  # noqa: N802
            # Header tuỳ biến buộc trình duyệt kiểm tra CORS -> trang web lạ không gọi được API này
            if self.headers.get("X-TCH") != "1":
                return self._json(403, {"error": "forbidden"})
            if urlparse(self.path).path.startswith("/api/brand/"):
                return self._brand_write(delete=False)
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

        def do_DELETE(self):  # noqa: N802
            if self.headers.get("X-TCH") != "1":
                return self._json(403, {"error": "forbidden"})
            return self._brand_write(delete=True)

        def _brand_write(self, delete: bool) -> None:
            """Tải lên (POST, thân là file ảnh) hoặc xoá (DELETE) ảnh nhân vật / logo."""
            slot = urlparse(self.path).path.rsplit("/", 1)[1]
            if brand is None or slot not in BRAND_SLOTS or not urlparse(self.path).path.startswith("/api/brand/"):
                return self._json(404, {"error": "not found"})
            try:
                if delete:
                    brand.delete(slot)
                else:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= BRAND_MAX_BYTES:
                        raise ValueError("Ảnh quá lớn (tối đa 3 MB) hoặc rỗng.")
                    brand.save(slot, self.rfile.read(length))
            except (ValueError, OSError) as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, brand.available())

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
            ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower()) or mimetypes.guess_type(full)[0] \
                or "application/octet-stream"
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
    ap.add_argument("--interval", type=float, default=None,
                    help="chu kỳ đọc cảm biến, giây (mặc định theo cài đặt, 15 giây; chỉnh trong CALIBRATE)")
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

    manager = None
    if args.demo:
        provider = sensors.DemoAuto()
    else:
        lhm_url = args.lhm_url or (DEFAULT_LHM_URL if IS_WINDOWS else None)
        provider = sensors.AutoProvider(lhm_url, args.lhm_auth, board_source_fn=lambda: store.get()["board_source"])
        if IS_WINDOWS and not args.no_manage_lhm and not args.lhm_url:
            manager = winsupport.LhmManager()
            threading.Thread(target=manager.ensure_running, daemon=True).start()
            if not winsupport.is_admin():
                print("Cảnh báo: app không chạy quyền Administrator – có thể thiếu nhiệt độ CPU/mainboard.")

    store = SettingsStore(settings_path())
    # Nhật ký nhiệt độ (DEMO dùng file riêng để không lẫn với số liệu thật)
    db = None
    try:
        db = history.HistoryDB(os.path.join(os.path.dirname(settings_path()),
                                            "history-demo.db" if args.demo else "history.db"))
        if args.demo:
            history.seed_demo(db)
    except Exception as exc:  # noqa: BLE001 - không ghi được nhật ký vẫn phải đo được nhiệt độ
        print("Không mở được nhật ký nhiệt độ:", exc)
        db = None
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

    def interval() -> float:
        if args.interval:  # tham số dòng lệnh chỉ áp dụng cho phiên này
            return min(300.0, max(0.5, args.interval))
        return float(store.get()["interval"])

    sampler = Sampler(provider, interval, store.get, status_fn=(lambda: manager.status) if manager else None,
                      extra_fn=lambda: {"overlay": None if ctl is None else ctl.visible}, db=db)
    # Đổi chu kỳ hoặc nguồn mainboard -> đo lại ngay
    store.listeners.append(lambda patch: ("interval" in patch or "board_source" in patch) and sampler.wake.set())
    if ctl is not None:
        ctl.on_change = sampler.republish  # nút OVERLAY trên giao diện cập nhật ngay
    sampler.start()

    def diag_fn() -> dict:
        import platform

        return {"app": APP_NAME, "version": APP_VERSION, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "os": platform.platform(), "python": sys.version.split()[0], "frozen": FROZEN,
                "admin": winsupport.is_admin() if IS_WINDOWS else None,
                "lhm_manager": None if manager is None else {"status": manager.status, "started_by_us": manager.started_by_us},
                "settings": store.get(), "snapshot": sampler.latest, **sensors.diagnostics(provider),
                "wmi": winsupport.wmi_temperature_scan() if IS_WINDOWS else None}

    # Chỉ lắng nghe 127.0.0.1: máy khác trong mạng không truy cập được.
    try:
        brand = BrandStore(os.path.join(os.path.dirname(settings_path()), "brand"))
        server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                     make_handler(sampler, resource_dir(), store, ctl, db, diag_fn, brand))
    except OSError as exc:
        fatal(f"Không mở được cổng {args.port} ({exc}). Hãy đóng ứng dụng đang dùng cổng này "
              f"hoặc chạy với tham số --port khác.")
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"{APP_NAME} {APP_VERSION} – thiết kế & phát triển: {APP_AUTHOR}")
    print(f"Đang chạy tại {url}  (Ctrl+C để thoát)")
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
        if db:
            db.close()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - hiện lỗi cho người dùng bản .exe
        import traceback
        traceback.print_exc()
        fatal(f"Lỗi không mong muốn: {exc}")
