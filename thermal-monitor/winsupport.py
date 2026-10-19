"""Hỗ trợ riêng cho Windows: tự tải / cấu hình / khởi động LibreHardwareMonitor, mở cửa sổ app, hộp thoại lỗi.

Chỉ dùng thư viện chuẩn để đóng gói thành một file .exe duy nhất.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

LHM_VERSION = "v0.9.6"
LHM_URL = ("https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/"
           f"{LHM_VERSION}/LibreHardwareMonitor.zip")
LHM_SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
LHM_EXE = "LibreHardwareMonitor.exe"
LHM_DATA_URL = "http://127.0.0.1:8085/data.json"

# Khoá cấu hình của LibreHardwareMonitor (file LibreHardwareMonitor.config cạnh exe)
LHM_SETTINGS = {
    "runWebServerMenuItem": "true",   # bật Remote Web Server
    "listenerIp": "127.0.0.1",        # chỉ máy này truy cập được, không lộ ra mạng LAN
    "listenerPort": "8085",
    "authenticationEnabled": "false",
    "startMinMenuItem": "true",       # khởi động thu nhỏ
    "minTrayMenuItem": "true",        # thu nhỏ xuống khay hệ thống
    "minCloseMenuItem": "true",       # bấm X = thu nhỏ, không tắt
}

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def app_dir() -> str:
    """Thư mục chứa exe (khi đóng gói) hoặc chứa app.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "ThermalCoreHUD")
    os.makedirs(path, exist_ok=True)
    return path


def lhm_dir() -> str:
    # Bản portable: thư mục lhm\ đặt cạnh exe được ưu tiên
    local = os.path.join(app_dir(), "lhm")
    if os.path.isfile(os.path.join(local, LHM_EXE)):
        return local
    return os.path.join(data_dir(), "lhm")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def message_box(text: str, title: str = "Thermal Core HUD", error: bool = True) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except (AttributeError, OSError):
        print(f"{title}: {text}", file=sys.stderr)


# --------------------------------------------------------------------------- LibreHardwareMonitor

def install_lhm_from_zip(data: bytes, dest: str, expected_sha256: str = LHM_SHA256) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != expected_sha256.lower():
        raise RuntimeError(f"SHA256 của LibreHardwareMonitor không khớp ({digest}); dừng để đảm bảo an toàn.")
    tmp = dest + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp)  # extractall tự loại bỏ đường dẫn tuyệt đối / ".."
    if not os.path.isfile(os.path.join(tmp, LHM_EXE)):
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Gói LibreHardwareMonitor không có {LHM_EXE}.")
    # Giữ lại file cấu hình cũ nếu có
    old_cfg = os.path.join(dest, "LibreHardwareMonitor.config")
    if os.path.isfile(old_cfg):
        shutil.copy2(old_cfg, os.path.join(tmp, "LibreHardwareMonitor.config"))
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


def download_lhm(dest: str, log=print) -> None:
    log(f"Đang tải LibreHardwareMonitor {LHM_VERSION}...")
    req = urllib.request.Request(LHM_URL, headers={"User-Agent": "ThermalCoreHUD"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    install_lhm_from_zip(data, dest)
    log(f"Đã cài LibreHardwareMonitor vào {dest}")


def write_lhm_config(path: str, settings: dict[str, str] = LHM_SETTINGS) -> None:
    """Cập nhật (không ghi đè toàn bộ) file cấu hình XML appSettings của LHM."""
    root = None
    if os.path.isfile(path):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            root = None
    if root is None or root.tag != "configuration":
        root = ET.Element("configuration")
    app = root.find("appSettings")
    if app is None:
        app = ET.SubElement(root, "appSettings")
    for key, value in settings.items():
        node = next((n for n in app.findall("add") if n.get("key") == key), None)
        if node is None:
            node = ET.SubElement(app, "add", key=key)
        node.set("value", value)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _tasklist_has(image: str) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], capture_output=True,
                             text=True, timeout=10, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return image.lower() in out.lower()


def stop_lhm() -> None:
    subprocess.run(["taskkill", "/IM", LHM_EXE, "/F"], capture_output=True, timeout=10, creationflags=_NO_WINDOW)


class LhmManager:
    """Đảm bảo LibreHardwareMonitor đã cài, đã bật web server và đang chạy."""

    def __init__(self, log=print):
        self.log = log
        self.started_by_us = False
        self.status = "Đang chuẩn bị LibreHardwareMonitor..."

    def ensure_running(self) -> None:
        folder = lhm_dir()
        exe = os.path.join(folder, LHM_EXE)
        cfg = os.path.join(folder, "LibreHardwareMonitor.config")
        try:
            if not os.path.isfile(exe):
                self.status = f"Đang tải LibreHardwareMonitor {LHM_VERSION} (lần đầu, khoảng 6 MB)..."
                download_lhm(folder, self.log)
            running = _tasklist_has(LHM_EXE)
            if running and not self._config_ok(cfg):
                # LHM ghi đè file cấu hình khi thoát -> phải dừng trước khi sửa
                self.log("Khởi động lại LibreHardwareMonitor để bật web server...")
                stop_lhm()
                running = False
            if not running:
                write_lhm_config(cfg)
                subprocess.Popen([exe], cwd=folder)
                self.started_by_us = True
                self.log("Đã khởi động LibreHardwareMonitor.")
            self._wait_web_server()
            self.status = None
        except Exception as exc:  # noqa: BLE001 - báo lỗi lên giao diện, không làm sập app
            self.status = f"Không chuẩn bị được LibreHardwareMonitor: {exc}"
            self.log(self.status)

    def _wait_web_server(self, timeout: float = 45) -> None:
        """LHM mất vài giây để quét phần cứng (lần đầu còn hỏi cài PawnIO) -> chờ web server sẵn sàng."""
        self.status = ("Đang khởi động LibreHardwareMonitor... Nếu được hỏi "
                       "\"PawnIO is not installed, do you want to install it?\" hãy bấm OK.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(LHM_DATA_URL, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        self.log("LibreHardwareMonitor chưa phản hồi sau 45 giây.")

    @staticmethod
    def _config_ok(path: str) -> bool:
        if not os.path.isfile(path):
            return False
        try:
            app = ET.parse(path).getroot().find("appSettings")
        except ET.ParseError:
            return False
        if app is None:
            return False
        values = {n.get("key"): n.get("value") for n in app.findall("add")}
        return values.get("runWebServerMenuItem") == "true" and values.get("listenerPort") == "8085"

    def shutdown(self) -> None:
        if self.started_by_us:
            stop_lhm()


# --------------------------------------------------------------------------- mở giao diện

def find_browser() -> str | None:
    roots = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
    for sub in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for r in roots:
            path = os.path.join(r, sub) if r else ""
            if path and os.path.isfile(path):
                return path
    return shutil.which("msedge") or shutil.which("chrome")


def open_unelevated(exe: str, args: str) -> bool:
    """Mở chương trình bằng quyền người dùng thường (qua Explorer), kể cả khi app đang chạy quyền Admin.

    Trình duyệt chạy quyền Administrator vừa rủi ro vừa dễ lỗi profile, nên tránh.
    """
    q = lambda s: s.replace("'", "''")  # noqa: E731 - escape chuỗi PowerShell
    cmd = f"(New-Object -ComObject Shell.Application).ShellExecute('{q(exe)}', '{q(args)}', '', 'open', 1)"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, timeout=15, creationflags=_NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
