"""Nguồn đọc nhiệt độ phần cứng cho VietTam Core Hub.

Mỗi provider trả về danh sách Sensor. Thứ tự ưu tiên khi chạy chế độ "auto":

* Windows : LibreHardwareMonitor (web server data.json) -> dự phòng nvidia-smi + ACPI (WMI)
* Linux   : /sys/class/hwmon -> bổ sung nvidia-smi nếu chưa có GPU
* Demo    : dữ liệu mô phỏng (chỉ khi chạy với --demo)

Không bao giờ tự chuyển sang dữ liệu giả khi không đọc được cảm biến thật.
"""

from __future__ import annotations

import base64
import glob
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

CATEGORIES = ("cpu", "gpu", "board", "storage", "other")


@dataclass
class Sensor:
    id: str
    name: str
    hardware: str
    category: str
    value: float | None
    min: float | None = None
    max: float | None = None
    warn: float | None = None   # ngưỡng do nhà sản xuất công bố (vd. ổ SSD), nếu có
    crit: float | None = None


@dataclass
class Reading:
    sensors: list[Sensor]
    source: str
    mode: str = "live"  # live | demo | nosensor
    messages: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)  # tải, công suất, xung nhịp, quạt, TjMax...


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


# ---------------------------------------------------------------------------
# Chọn cảm biến chính cho từng nhóm
# ---------------------------------------------------------------------------

_PRIMARY_PREFS = {
    "cpu": ["cpu package", "core (tctl/tdie)", "tctl/tdie", "tdie", "tctl",
            "package id 0", "package", "core average", "core max", "cpu"],
    "gpu": ["gpu core", "gpu temperature", "edge", "junction", "gpu hot spot", "core", "gpu"],
    "board": ["motherboard", "mainboard", "system", "systin", "pch", "chipset",
              "acpi", "thermal zone", "temperature", "temp", "auxiliary"],
}


def _is_discrete_gpu(s: Sensor) -> bool:
    text = f"{s.id} {s.hardware}".lower()
    return any(k in text for k in ("nvidia", "geforce", "rtx", "gtx", "radeon", "gpu-amd", "amdgpu"))


def pick_primary(sensors: list[Sensor]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for cat in ("cpu", "gpu", "board"):
        cands = [s for s in sensors if s.category == cat]
        if cat == "board" and not any(s.value is not None for s in cands):
            # Không có cảm biến mainboard, hoặc có nhưng chỉ trả về giá trị rác (0°C...)
            # Laptop (vd. ThinkPad) không có chip SuperIO và ACPI có thể bị khoá ->
            # dùng nhiệt độ SSD NVMe (nằm trên mainboard) làm nguồn thay thế; giao diện ghi rõ "thay thế".
            cands = [s for s in sensors if s.category == "storage" and s.value is not None] or cands
        if cat == "gpu":
            # Ưu tiên GPU rời; nếu GPU rời đang ngủ (không có giá trị) thì vẫn giữ nó
            # để giao diện hiển thị trạng thái SLEEP thay vì nhảy sang GPU khác.
            discrete = [s for s in cands if _is_discrete_gpu(s)]
            cands = discrete or cands
        live = [s for s in cands if s.value is not None]
        pool = live or cands
        chosen = None
        if cat == "board" and pool and pool[0].category == "storage":
            # Nhiệt độ tổng của ổ (không phải cảm biến phụ "Temperature #n")
            chosen = next((s for s in pool if s.name.lower() in ("temperature", "composite temperature", "composite")),
                          None)
        for pref in ([] if chosen else _PRIMARY_PREFS[cat]):
            chosen = next((s for s in pool if pref in s.name.lower()), None)
            if chosen:
                break
        if chosen is None and live:
            chosen = max(live, key=lambda s: s.value)
        if chosen is None and pool:
            chosen = pool[0]
        result[cat] = chosen.id if chosen else None
    return result


# ---------------------------------------------------------------------------
# LibreHardwareMonitor (Windows) – đọc từ Remote Web Server: http://localhost:8085/data.json
# ---------------------------------------------------------------------------

# Tên nút nhóm theo loại cảm biến của LHM (UI/TypeNode.cs), kèm vài tên cũ
_GROUP_NAMES = {"voltages", "currents", "capacities", "clocks", "load", "loads", "temperatures", "fans", "flows",
                "controls", "levels", "powers", "data", "smalldata", "factors", "frequencies", "throughput",
                "times", "timings", "noise levels", "noise", "conductivities", "conductances",
                "humidity levels", "humidity", "energy"}

_VALUE_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*°?\s*([CF])\b")


def parse_lhm_value(text) -> float | None:
    """'45.0 °C' / '45,0 °C' / '113.0 °F' -> độ C."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _VALUE_RE.search(str(text))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    if m.group(2) == "F":
        v = (v - 32) * 5 / 9
    return v


_CLASSIFY_RULES = [
    ("cpu", re.compile(r"intelcpu|amdcpu|^/cpu|cpu\.png")),
    ("gpu", re.compile(r"^/gpu|nvidia\.png|ati\.png|amd\.png|intel\.png")),
    ("board", re.compile(r"^/lpc|^/motherboard|^/mainboard|^/ec\b|mainboard\.png|chip\.png")),
    ("storage", re.compile(r"^/nvme|^/hdd|^/ssd|^/storage|hdd\.png")),
    # Tên phần cứng (chỉ dùng khi không có HardwareId / icon)
    ("gpu", re.compile(r"nvidia|geforce|radeon|\bgpu\b|graphics")),
    ("cpu", re.compile(r"\bcpu\b|ryzen|core\(tm\)|core i\d|core ultra|xeon|celeron|pentium")),
    ("board", re.compile(r"superio|embedded controller|nct\d|ite it|it8\d|fintek|winbond|acpi|thermal zone|pch|chipset")),
    ("storage", re.compile(r"nvme|ssd|\bdisk\b|samsung|wdc|kingston")),
]


def _raw_or_text(node: dict, key: str) -> float | None:
    """LHM >= 0.9.x có RawValue (số, luôn là °C); bản cũ chỉ có chuỗi đã định dạng."""
    raw = node.get("Raw" + key)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return parse_lhm_value(node.get(key))


def _classify(hints: list[str]) -> str:
    """Phân loại theo thứ tự quy tắc: ID/icon có cấu trúc trước, tên tự do sau.

    Nhờ vậy "AMD Ryzen 7 5800H with Radeon Graphics" (icon cpu.png) vẫn là CPU.
    """
    texts = [h.lower() for h in hints if h]
    for cat, rx in _CLASSIFY_RULES:
        if any(rx.search(t) for t in texts):
            return cat
    return "other"


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_UNIT_TYPES = [("°C", "temperature"), ("°F", "temperature"), ("%", "load"), ("RPM", "fan"),
               ("MHz", "clock"), ("GHz", "clock"), (" W", "power")]
# Ổ cứng công bố ngưỡng cố định dưới dạng "cảm biến" nhiệt độ -> không phải nhiệt độ đo được
_LIMIT_RE = re.compile(r"warning temperature|critical temperature|threshold|temperature limit", re.I)
# Ngoài khoảng này coi như cảm biến lỗi/kênh không dùng. 0°C là giá trị "trống" phổ biến của chip
# giám sát mainboard (SuperIO); 125–128°C cũng là mã "không có cảm biến" -> mainboard giới hạn chặt hơn.
TEMP_VALID = (0.0, 150.0)
TEMP_VALID_BOARD = (0.0, 125.0)
_CORE_RE = re.compile(r"^((cpu |p-|e-)?core)( #?\d+)?$")  # "CPU Core #1", "P-Core #2", "Core 0" (Linux)
# (loại, tên cảm biến LHM) -> trường sức khoẻ ổ đĩa
_DRIVE_FIELDS = {
    ("level", "life"): "life",                      # % tuổi thọ còn lại
    ("level", "percentage used"): "used_pct",       # % tuổi thọ đã dùng (NVMe)
    ("level", "available spare"): "spare",
    ("level", "available spare threshold"): "spare_min",
    ("data", "data written"): "written_gb",
    ("data", "data read"): "read_gb",
    ("factor", "power on hours"): "hours",
    ("factor", "power on count"): "power_cycles",
    ("load", "used space"): "space_used",
    ("data", "total space"): "total_gb",
}


def _num(node: dict, key: str) -> float | None:
    raw = node.get("Raw" + key)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    m = _NUM_RE.search(str(node.get(key) or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _plausible(v: float | None, category: str = "") -> float | None:
    lo, hi = TEMP_VALID_BOARD if category == "board" else TEMP_VALID
    return v if v is not None and lo < v < hi else None


def _leaf_type(node: dict) -> str:
    stype = str(node.get("Type", "")).lower()
    if stype:
        return stype
    text = str(node.get("Value") or "")
    return next((t for unit, t in _UNIT_TYPES if unit in text), "")


def parse_lhm(root: dict) -> tuple[list[Sensor], dict]:
    """Đọc cây data.json của LibreHardwareMonitor -> (cảm biến nhiệt độ, thông số phụ)."""
    sensors: list[Sensor] = []
    leaves: list[tuple[str, str, str, str, dict]] = []  # (loại, tên, phần cứng, nhóm, node)
    limits: dict[str, dict] = {}

    def is_group(node: dict) -> bool:
        return str(node.get("Text", "")).strip().lower() in _GROUP_NAMES and not node.get("HardwareId")

    def walk(node: dict, hw_path: list[str], hw_hints: list, depth: int) -> None:
        children = node.get("Children") or []
        if not children:
            stype = _leaf_type(node)
            if not stype:
                return
            name = str(node.get("Text", "?"))
            hardware = " / ".join(hw_path) or "Unknown"
            category = _classify([str(node.get("SensorId", ""))] + [h for hw in hw_hints for h in hw])
            if category == "other" and name.lower().startswith(("cpu", "core")):
                category = "cpu"
            leaves.append((stype, name, hardware, category, node))
            return
        path, hints = hw_path, hw_hints
        # depth 0 = "Sensor" (gốc), depth 1 = tên máy tính -> không đưa vào đường dẫn phần cứng
        if depth >= 2 and not is_group(node):
            path = hw_path + [str(node.get("Text", "?"))]
            hints = hw_hints + [(str(node.get("HardwareId", "")), str(node.get("ImageURL", "")),
                                 str(node.get("Text", "")))]
        for child in children:
            walk(child, path, hints, depth + 1)

    walk(root, [], [], 0)

    distances: dict[str, float] = {}
    for stype, name, hardware, category, node in leaves:
        if stype != "temperature":
            continue
        lname = name.lower()
        if "distance to tjmax" in lname:  # khoảng cách tới ngưỡng, không phải nhiệt độ
            v = _raw_or_text(node, "Value")
            if v is not None:
                distances[name[: lname.index(" distance")]] = v
            continue
        if _LIMIT_RE.search(name):
            v = _raw_or_text(node, "Value")
            key = "crit" if "critical" in lname else "warn"
            if v is not None:
                limits.setdefault(hardware, {})[key] = v
            continue
        sid = node.get("SensorId") or f"lhm:{hardware}/{name}"
        sensors.append(Sensor(
            id=str(sid), name=name, hardware=hardware, category=category,
            value=_round(_plausible(_raw_or_text(node, "Value"), category)),
            min=_round(_plausible(_raw_or_text(node, "Min"), category)),
            max=_round(_plausible(_raw_or_text(node, "Max"), category)),
        ))
    for s in sensors:
        lim = limits.get(s.hardware)
        if lim:
            s.warn, s.crit = _round(lim.get("warn")), _round(lim.get("crit"))

    metrics: dict = {"fans": []}
    clocks = []
    core_clock: dict[str, float] = {}
    core_load: dict[int, list[float]] = {}
    drives: dict[str, dict] = {}
    for stype, name, hardware, category, node in leaves:
        v = _num(node, "Value")
        lname = name.lower()
        if v is None:
            continue
        if category == "storage":
            key = _DRIVE_FIELDS.get((stype, lname))
            if key:
                drives.setdefault(hardware, {"hardware": hardware})[key] = round(v, 1)
            continue
        if stype == "load" and category == "cpu" and lname in ("cpu total", "total"):
            metrics["cpu_load"] = round(v, 1)
        elif stype == "load" and category == "cpu" and (m := re.match(r"cpu core #(\d+)( thread #\d+)?$", lname)):
            core_load.setdefault(int(m.group(1)), []).append(v)
        elif stype == "power" and category == "cpu" and lname in ("cpu package", "package"):
            metrics["cpu_power"] = round(v, 1)
        elif stype == "clock" and category == "cpu" and _CORE_RE.match(lname):
            clocks.append(v)
            core_clock[lname.removeprefix("cpu ")] = v  # "Core #1" (LHM cũ) == "CPU Core #1"
        elif stype == "load" and category == "gpu" and lname in ("gpu core", "d3d 3d"):
            metrics.setdefault("gpu_load", round(v, 1))
        elif stype == "fan" and v > 0:
            metrics["fans"].append({"name": name, "hardware": hardware, "rpm": round(v)})
    if clocks:
        metrics["cpu_clock"] = round(sum(clocks) / len(clocks))
        metrics["cpu_clock_max"] = round(max(clocks))  # nhân nhanh nhất – dùng để nhận biết hạ xung
    # Từng nhân: nhiệt độ + tải + xung. Tải được đặt tên theo số thứ tự nhân ("CPU Core #k Thread #m")
    # còn nhiệt độ/xung có thể là "P-Core #1"/"E-Core #1" -> ghép theo THỨ TỰ nhân (LHM liệt kê cùng thứ tự).
    # Không dùng chỉ số trong SensorId: "Core Max"/"Core Average" có thể đứng trước và làm lệch chỉ số.
    cores = []
    for pos, s in enumerate(x for x in sensors if x.category == "cpu" and _CORE_RE.match(x.name.lower())):
        k = pos + 1
        loads = core_load.get(k)
        cores.append({"name": s.name, "temp": s.value,
                      "load": round(sum(loads) / len(loads), 1) if loads else None,
                      "clock": round(c) if (c := core_clock.get(s.name.lower().removeprefix("cpu "))) else None})
    if cores:
        metrics["cores"] = cores
    # Ổ cứng: sức khoẻ, dữ liệu đã ghi, giờ hoạt động...
    for s in sensors:
        if s.category == "storage" and s.hardware in drives and s.name.lower() in ("temperature", "composite temperature"):
            drives[s.hardware].update(temp=s.value, warn=s.warn, crit=s.crit)
    if drives:
        metrics["drives"] = list(drives.values())
    # TjMax = nhiệt độ lõi + khoảng cách tới TjMax của chính lõi đó
    by_name = {s.name: s.value for s in sensors if s.category == "cpu"}
    tj = [by_name[n] + d for n, d in distances.items() if by_name.get(n) is not None]
    if tj:
        metrics["tjmax"] = round(max(tj))
    return sensors, metrics


def parse_lhm_tree(root: dict) -> list[Sensor]:
    return parse_lhm(root)[0]


class LhmHttpProvider:
    name = "LibreHardwareMonitor"

    def __init__(self, url: str = "http://localhost:8085/data.json", auth: str | None = None, timeout: float = 1.5):
        self.url = url
        self.timeout = timeout
        self.headers = {"Accept": "application/json"}
        if auth:
            self.headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()

    def read_raw(self) -> dict:
        req = urllib.request.Request(self.url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8-sig"))

    def read(self) -> tuple[list[Sensor], dict]:
        return parse_lhm(self.read_raw())


# ---------------------------------------------------------------------------
# Bộ nhớ đệm chạy nền cho các lệnh chậm (nvidia-smi, PowerShell)
# ---------------------------------------------------------------------------

class _BackgroundPoller:
    def __init__(self, fn, interval: float):
        self._fn = fn
        self._interval = interval
        self._lock = threading.Lock()
        self._value: list[Sensor] = []
        self.error: str | None = None
        self._started = False

    def get(self) -> list[Sensor]:
        if not self._started:
            self._started = True
            self._tick()  # lần đầu chạy đồng bộ để có dữ liệu ngay
            threading.Thread(target=self._loop, daemon=True).start()
        with self._lock:
            return list(self._value)

    def _tick(self) -> None:
        try:
            value, self.error = self._fn(), None
        except Exception as exc:  # noqa: BLE001 - lỗi nguồn phụ không được làm sập app
            value, self.error = [], str(exc)
        with self._lock:
            self._value = value

    def _loop(self) -> None:
        while True:
            time.sleep(self._interval)
            self._tick()


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: float) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip() or f"{cmd[0]} exit {out.returncode}")
    return out.stdout


def parse_nvidia_smi(text: str) -> list[Sensor]:
    sensors = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        idx, name, temp = parts[0], parts[1], parts[2]
        try:
            value = float(temp)
        except ValueError:
            value = None  # "[N/A]" khi GPU đang ngủ
        sensors.append(Sensor(id=f"nvsmi/{idx}", name="GPU Core", hardware=name, category="gpu", value=value))
    return sensors


class NvidiaSmiProvider:
    name = "nvidia-smi"

    def __init__(self, interval: float = 2.0):
        self.exe = shutil.which("nvidia-smi")
        self._poller = _BackgroundPoller(self._query, interval)

    @property
    def available(self) -> bool:
        return self.exe is not None

    def _query(self) -> list[Sensor]:
        out = _run([self.exe, "--query-gpu=index,name,temperature.gpu", "--format=csv,noheader,nounits"], 4)
        return parse_nvidia_smi(out)

    def read(self) -> list[Sensor]:
        return self._poller.get() if self.exe else []


class WindowsAcpiProvider:
    """Nhiệt độ vùng nhiệt ACPI qua WMI. Cần quyền Administrator, nhiều laptop không hỗ trợ."""

    name = "ACPI (WMI)"
    _PS = ("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
           "ForEach-Object { \"$($_.InstanceName)|$($_.CurrentTemperature)\" }")

    def __init__(self, interval: float = 5.0):
        self._poller = _BackgroundPoller(self._query, interval)

    def _query(self) -> list[Sensor]:
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", self._PS], 8)
        sensors = []
        for i, line in enumerate(l for l in out.splitlines() if "|" in l):
            inst, raw = line.rsplit("|", 1)
            try:
                value = float(raw) / 10 - 273.15  # đơn vị gốc: 1/10 độ Kelvin
            except ValueError:
                continue
            if not -20 < value < 150:
                continue
            zone = re.search(r"ThermalZone\\([^\\_]+)", inst)
            sensors.append(Sensor(id=f"acpi/{i}", name=f"ACPI {zone.group(1) if zone else i}",
                                  hardware=f"Embedded Controller · {inst.strip() or 'ACPI'}",
                                  category="board", value=_round(value)))
        return sensors

    @property
    def error(self) -> str | None:
        return self._poller.error

    def read(self) -> list[Sensor]:
        return self._poller.get()


# ---------------------------------------------------------------------------
# Linux – /sys/class/hwmon
# ---------------------------------------------------------------------------

_HWMON_CAT = [
    (re.compile(r"^(coretemp|k10temp|zenpower|cpu_thermal|x86_pkg_temp|soc_thermal)"), "cpu"),
    (re.compile(r"^(amdgpu|radeon|nouveau|i915|xe)$"), "gpu"),
    (re.compile(r"^(nvme|drivetemp)"), "storage"),
    (re.compile(r"^(acpitz|pch_|nct|it8|asus|thinkpad|dell_smm|hp|f71|w83|applesmc|gigabyte|sch5)"), "board"),
]


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


class LinuxCpuLoad:
    """% CPU bận giữa hai lần đọc /proc/stat."""

    def __init__(self, path: str = "/proc/stat"):
        self.path = path
        self.prev: tuple[int, int] | None = None

    def read(self) -> float | None:
        line = (_read_text(self.path) or "").splitlines()[:1]
        if not line or not line[0].startswith("cpu "):
            return None
        vals = [int(x) for x in line[0].split()[1:]]
        idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
        prev, self.prev = self.prev, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


class LinuxHwmonProvider:
    name = "Linux hwmon"

    def __init__(self, root: str = "/sys/class/hwmon"):
        self.root = root

    def fans(self) -> list[dict]:
        out = []
        for hw in sorted(glob.glob(os.path.join(self.root, "hwmon*"))):
            chip = _read_text(os.path.join(hw, "name")) or os.path.basename(hw)
            for inp in sorted(glob.glob(os.path.join(hw, "fan*_input"))):
                try:
                    rpm = int(_read_text(inp) or "0")
                except ValueError:
                    continue
                if rpm > 0:
                    base = inp[: -len("_input")]
                    out.append({"name": _read_text(base + "_label") or os.path.basename(base), "hardware": chip, "rpm": rpm})
        return out

    def read(self) -> list[Sensor]:
        sensors = []
        for hw in sorted(glob.glob(os.path.join(self.root, "hwmon*"))):
            chip = _read_text(os.path.join(hw, "name")) or os.path.basename(hw)
            category = next((cat for rx, cat in _HWMON_CAT if rx.search(chip)), "other")
            for inp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
                raw = _read_text(inp)
                if raw is None:
                    continue
                try:
                    value = int(raw) / 1000
                except ValueError:
                    continue
                base = inp[: -len("_input")]
                label = _read_text(base + "_label") or os.path.basename(base)
                sensors.append(Sensor(id=f"hwmon/{os.path.basename(hw)}/{os.path.basename(base)}",
                                      name=label, hardware=chip, category=category, value=_round(value)))
        return sensors


# ---------------------------------------------------------------------------
# Demo – mô phỏng có tải tăng/giảm, chỉ dùng khi chạy --demo
# ---------------------------------------------------------------------------

class DemoProvider:
    name = "Demo simulator"

    def __init__(self):
        self.t0 = time.time()
        self.state = {"cpu": 48.0, "gpu": 44.0, "board": 41.0, "nvme": 38.0}
        self.load = 0.3
        self.core_temps = [45.0] * 6

    def metrics(self) -> dict:
        cores = [{"name": f"CPU Core #{i + 1}", "temp": c, "load": round(min(100, self.load * 100 + 8 * i - 15), 1),
                  "clock": round(1800 + 2600 * self.load - 40 * i)} for i, c in enumerate(self.core_temps)]
        throttled = self.state["cpu"] >= 92  # mô phỏng hạ xung khi quá nóng
        return {"cpu_load": round(self.load * 100, 1), "cpu_power": round(4 + 36 * self.load, 1),
                "cpu_clock": round(1800 + 2600 * self.load), "cpu_clock_max": 2700 if throttled else 4400, "gpu_load": round(max(0.0, self.state["gpu"] - 40) * 2, 1),
                "tjmax": 100, "fans": [{"name": "CPU Fan", "hardware": "Demo", "rpm": round(1500 + 3200 * self.load)}],
                "cores": cores,
                "drives": [{"hardware": "Samsung SSD 980 1TB (demo)", "life": 97.0, "used_pct": 3.0, "spare": 100.0,
                            "spare_min": 10.0, "written_gb": 18432.0, "read_gb": 25100.0, "hours": 6120.0,
                            "power_cycles": 1450.0, "space_used": 61.5, "total_gb": 931.5,
                            "temp": _round(self.state["nvme"]), "warn": 82.0, "crit": 85.0}]}

    def read(self) -> list[Sensor]:
        t = time.time() - self.t0
        load = 0.5 + 0.5 * math.sin(t / 170) * math.sin(t / 53)  # 0..1, các đợt tải (chu kỳ phút)
        self.load = load
        targets = {"cpu": 42 + 52 * load, "gpu": 40 + 40 * max(0.0, math.sin(t / 23)),
                   "board": 38 + 12 * load, "nvme": 36 + 8 * load}
        speed = {"cpu": 0.35, "gpu": 0.15, "board": 0.05, "nvme": 0.05}
        for k, target in targets.items():
            self.state[k] += (target - self.state[k]) * speed[k] + random.uniform(-0.6, 0.6)
        cpu = self.state["cpu"]
        self.core_temps = [_round(cpu - 4 + 1.5 * i + random.uniform(-1.5, 1.5)) for i in range(6)]
        sensors = [
            Sensor("demo/cpu/package", "CPU Package", "Intel Core i7-12700H (demo)", "cpu", _round(cpu)),
            *[Sensor(f"demo/cpu/core{i}", f"CPU Core #{i + 1}", "Intel Core i7-12700H (demo)", "cpu", t)
              for i, t in enumerate(self.core_temps)],
            Sensor("demo/gpu/core", "GPU Core", "NVIDIA GeForce RTX 3060 Laptop (demo)", "gpu", _round(self.state["gpu"])),
            Sensor("demo/gpu/hotspot", "GPU Hot Spot", "NVIDIA GeForce RTX 3060 Laptop (demo)", "gpu",
                   _round(self.state["gpu"] + 9)),
            Sensor("demo/board/sys", "Motherboard", "Embedded Controller (demo)", "board", _round(self.state["board"])),
            Sensor("demo/board/pch", "PCH", "Embedded Controller (demo)", "board", _round(self.state["board"] + 6)),
            Sensor("demo/nvme", "Temperature", "Samsung SSD 980 1TB (demo)", "storage", _round(self.state["nvme"]),
                   warn=82.0, crit=85.0),
        ]
        return sensors


# ---------------------------------------------------------------------------
# Bộ tổng hợp
# ---------------------------------------------------------------------------

_HW_BOARD_RE = re.compile(r"nuvoton|nct\d|\bite\b|it8\d|fintek|winbond|smsc|super ?i/?o|\blpc\b|embedded controller|"
                          r"^system|motherboard|mainboard|chipset|\bpch\b|acpi|\blenovo\b|\basus\b|\bgigabyte\b|\bmsi\b|"
                          r"\basrock\b|\bdell\b|\bhp\b")


def hwinfo_category(device: str) -> str:
    """Nhóm của một thiết bị trong HWiNFO ("CPU [#0]: …", "GPU [#0]: …", "S.M.A.R.T.: …", "LENOVO 310B (Nuvoton …)")."""
    d = device.lower()
    if d.startswith("cpu") or "cpu [" in d:
        return "cpu"
    if d.startswith("gpu") or "gpu [" in d:
        return "gpu"
    if d.startswith(("s.m.a.r.t", "drive", "smart")):
        return "storage"
    if _HW_BOARD_RE.search(d):
        return "board"
    return "other"


def hwinfo_board_sensors(temps: list[dict]) -> list[Sensor]:
    """Nhiệt độ mainboard (và các nhiệt độ chưa rõ nhóm) từ HWiNFO. CPU/GPU/ổ đã có từ LHM nên bỏ qua."""
    out = []
    unnamed = 0
    for t in temps:
        cat = hwinfo_category(t["device"])
        if cat not in ("board", "other"):
            continue
        name = t["label"]
        if not name:
            # Kênh chip không có tên (vd. NCT6685D của Lenovo: 76/87/66°C) – thường là chân bỏ trống,
            # không rõ đo gì -> vẫn hiện trong SENSOR ARRAY nhưng không dùng làm nhiệt độ mainboard.
            unnamed += 1
            name, cat = f"Kênh không tên #{unnamed}", "other"
        out.append(Sensor(id=f"hwinfo/{t['key']}", name=name, hardware=f"HWiNFO · {t['device']}",
                          category=cat, value=_round(_plausible(t["value"], cat))))
    return out


class AutoProvider:
    def __init__(self, lhm_url: str | None, lhm_auth: str | None = None, board_source_fn=None):
        self.board_source_fn = board_source_fn or (lambda: "auto")  # "auto" (A) | "hwinfo" (B)
        is_windows = sys.platform.startswith("win")
        self.lhm = LhmHttpProvider(lhm_url, lhm_auth) if lhm_url else None
        self.hwmon = LinuxHwmonProvider() if sys.platform.startswith("linux") else None
        self.nvsmi = NvidiaSmiProvider()
        self.acpi = WindowsAcpiProvider() if is_windows else None
        self.cpu_load = LinuxCpuLoad() if sys.platform.startswith("linux") else None

    def read(self) -> Reading:
        sensors: list[Sensor] = []
        sources: list[str] = []
        messages: list[str] = []
        metrics: dict = {"fans": []}

        if self.lhm:
            try:
                got, metrics = self.lhm.read()
                sensors += got
                sources.append(self.lhm.name)
                if not got:
                    messages.append("LibreHardwareMonitor đang chạy nhưng không trả về cảm biến nhiệt độ nào.")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                messages.append(f"Không kết nối được LibreHardwareMonitor ({self.lhm.url}): {exc}. "
                                "Mở LibreHardwareMonitor bằng quyền Administrator và bật "
                                "Options → Remote Web Server → Run.")

        if self.hwmon:
            got = self.hwmon.read()
            if got:
                sensors += got
                sources.append(self.hwmon.name)
            metrics["fans"] += self.hwmon.fans()
        if self.cpu_load:
            load = self.cpu_load.read()
            if load is not None:
                metrics.setdefault("cpu_load", load)

        has_gpu = any(s.category == "gpu" and s.value is not None for s in sensors)
        if not has_gpu and self.nvsmi.available:
            got = self.nvsmi.read()
            if got:
                sensors += got
                sources.append(self.nvsmi.name)

        # Nguồn B: nhiệt độ mainboard từ HWiNFO (nhận diện được nhiều chip mà LHM chưa hỗ trợ)
        board_src = {"selected": self.board_source_fn(), "active": False}
        if board_src["selected"] == "hwinfo":
            try:
                import hwinfo

                data = hwinfo.parse(hwinfo.read_shared_memory())
                hw = hwinfo_board_sensors(hwinfo.temperatures(data))
                if hwinfo.is_stale(data):
                    raise hwinfo.HwinfoUnavailable("HWiNFO không còn cập nhật số liệu (đang tạm dừng hoặc bị treo).")
                if not any(x.category == "board" and x.value is not None for x in hw):
                    raise hwinfo.HwinfoUnavailable("HWiNFO đang chạy nhưng không có nhiệt độ mainboard cho máy này.")
                # Đã có nguồn B: bỏ cảm biến mainboard của LHM để đồng hồ MAINBOARD dùng đúng số của HWiNFO
                sensors = [x for x in sensors if x.category != "board"] + hw
                sources.append("HWiNFO")
                board_src["active"] = True
            except Exception as exc:  # noqa: BLE001 - nguồn B lỗi thì quay về A, không làm dừng việc đo
                board_src["error"] = str(exc)
                messages.append(f"Nguồn mainboard B (HWiNFO): {exc} Đang tạm dùng nguồn A.")
        metrics["board_source"] = board_src

        # Laptop thường không có chip SuperIO -> thử vùng nhiệt ACPI làm nhiệt độ "mainboard".
        if self.acpi and not any(s.category == "board" for s in sensors):
            got = self.acpi.read()
            if got:
                sensors += got
                sources.append(self.acpi.name)
            elif self.acpi.error and not any(s.category == "storage" for s in sensors):
                # Có SSD làm nguồn thay thế thì không cần cảnh báo
                messages.append("Không có cảm biến mainboard; ACPI/WMI cũng không đọc được "
                                "(cần chạy app bằng quyền Administrator, hoặc máy không hỗ trợ).")

        mode = "live" if sensors else "nosensor"
        if not sensors:
            messages.append("Chưa đọc được cảm biến nhiệt độ nào trên máy này.")
        return Reading(sensors=sensors, source=" + ".join(sources) or "—", mode=mode, messages=messages,
                       metrics=metrics)


def diagnostics(provider) -> dict:
    """Dữ liệu thô để chẩn đoán vì sao thiếu cảm biến (vd. LHM có nhận ra chip mainboard không)."""
    out: dict = {"provider": type(provider).__name__}
    lhm = getattr(provider, "lhm", None)
    if lhm is not None:
        out["lhm_url"] = lhm.url
        try:
            out["lhm_data"] = lhm.read_raw()
        except Exception as exc:  # noqa: BLE001 - ghi lại lỗi để chẩn đoán
            out["lhm_error"] = str(exc)
    acpi = getattr(provider, "acpi", None)
    if acpi is not None:
        out["acpi"] = {"values": [asdict(s) for s in acpi.read()], "error": acpi.error}
    if sys.platform.startswith("win"):
        try:
            import hwinfo

            data = hwinfo.parse(hwinfo.read_shared_memory())
            out["hwinfo"] = [{**t, "category": hwinfo_category(t["device"])} for t in hwinfo.temperatures(data)]
        except Exception as exc:  # noqa: BLE001 - ghi lại lý do để chẩn đoán
            out["hwinfo_error"] = str(exc)
    hwmon = getattr(provider, "hwmon", None)
    if hwmon is not None:
        out["hwmon"] = [asdict(s) for s in hwmon.read()]
    return out


class DemoAuto:
    def __init__(self):
        self.p = DemoProvider()

    def read(self) -> Reading:
        return Reading(sensors=self.p.read(), source=self.p.name, mode="demo", metrics=self.p.metrics(),
                       messages=["Đang chạy chế độ DEMO – số liệu mô phỏng, không phải nhiệt độ thật."])


def add_igpu_estimate(sensors: list[Sensor]) -> list[Sensor]:
    """Intel iGPU (UHD/Iris Xe) không có cảm biến nhiệt riêng: nó nằm chung đế (die) với CPU,
    nên nhiệt độ CPU Package là thước đo sát nhất. Chỉ thêm khi máy hoàn toàn không có cảm biến GPU."""
    if any(s.category == "gpu" for s in sensors):
        return sensors
    pkg = next((s for s in sensors if s.category == "cpu" and s.name.lower() in ("cpu package", "package id 0")), None)
    if pkg is None:
        return sensors
    ident = f"{pkg.id} {pkg.hardware}".lower()
    if "intel" not in ident and "coretemp" not in ident:
        return sensors
    return sensors + [Sensor(id="derived/igpu", name="iGPU ≈ CPU Package", category="gpu",
                             hardware=f"Intel iGPU – chung đế với {pkg.hardware} (ước tính)",
                             value=pkg.value, min=pkg.min, max=pkg.max)]


def snapshot(reading: Reading) -> dict:
    if reading.mode == "live":
        reading.sensors = add_igpu_estimate(reading.sensors)
    if "cores" not in reading.metrics:  # Linux: lấy nhiệt độ từng nhân từ coretemp ("Core 0"...)
        cores = [{"name": s.name, "temp": s.value, "load": None, "clock": None} for s in reading.sensors
                 if s.category == "cpu" and _CORE_RE.match(s.name.lower())]
        if cores:
            reading.metrics["cores"] = cores
    return {
        "ts": time.time(),
        "source": reading.source,
        "mode": reading.mode,
        "messages": reading.messages,
        "sensors": [asdict(s) for s in reading.sensors],
        "metrics": reading.metrics,
        "primary": pick_primary(reading.sensors),
    }
