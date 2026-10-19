"""Đọc cảm biến từ HWiNFO qua bộ nhớ chia sẻ (Shared Memory) – nguồn B cho nhiệt độ mainboard.

HWiNFO nhận diện được rất nhiều chip giám sát mainboard mà LibreHardwareMonitor chưa hỗ trợ
(vd. Lenovo ThinkCentre). Người dùng cần chạy HWiNFO và bật "Shared Memory Support";
bản miễn phí tự tắt chia sẻ sau 12 giờ (phải bật lại bằng tay).

Bố cục bộ nhớ "Global\\HWiNFO_SENS_SM2" (đóng gói 1 byte):
  header : signature u32 ('HWiS'; 'DEAD' = đã tắt), version u32, revision u32, poll_time i64,
           offset/size/count của mảng sensor (3×u32), offset/size/count của mảng reading (3×u32)
  sensor : id u32, instance u32, name_orig char[128], name_user char[128], ...
  reading: type u32 (1 = nhiệt độ), sensor_index u32, id u32, label_orig char[128], label_user char[128],
           unit char[16], value f64, min f64, max f64, avg f64, ...
"""

from __future__ import annotations

import struct
import sys
import time

MAP_NAMES = ("Global\\HWiNFO_SENS_SM2", "HWiNFO_SENS_SM2")
SIG_ACTIVE = b"HWiS"
SIG_DEAD = b"DEAD"
HEADER = struct.Struct("<4sIIqIIIIII")  # 44 byte
READING_TEMP = 1
READING_FAN = 3


class HwinfoUnavailable(Exception):
    """HWiNFO chưa chạy / chưa bật Shared Memory / đã hết 12 giờ (bản miễn phí)."""


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("cp1252", errors="replace").strip()


def parse(buf: bytes) -> dict:
    """Bộ nhớ chia sẻ -> {"poll_time", "sensors": [...], "readings": [...]}. Tách riêng để kiểm thử được."""
    if len(buf) < HEADER.size:
        raise HwinfoUnavailable("Bộ nhớ chia sẻ HWiNFO rỗng.")
    sig, _ver, _rev, poll, s_off, s_size, s_num, r_off, r_size, r_num = HEADER.unpack_from(buf, 0)
    if sig == SIG_DEAD:
        raise HwinfoUnavailable("HWiNFO đã tắt chia sẻ dữ liệu (bản miễn phí tự tắt sau 12 giờ) – "
                                "vào HWiNFO bật lại Shared Memory Support.")
    if sig != SIG_ACTIVE:
        raise HwinfoUnavailable("Dữ liệu HWiNFO không hợp lệ.")
    sensors = []
    for i in range(s_num):
        o = s_off + i * s_size
        sid, inst = struct.unpack_from("<II", buf, o)
        orig, user = _cstr(buf[o + 8:o + 136]), _cstr(buf[o + 136:o + 264])
        sensors.append({"id": sid, "instance": inst, "name": user or orig, "name_orig": orig})
    readings = []
    for i in range(r_num):
        o = r_off + i * r_size
        rtype, sidx, rid = struct.unpack_from("<III", buf, o)
        label_orig, label_user = _cstr(buf[o + 12:o + 140]), _cstr(buf[o + 140:o + 268])
        unit = _cstr(buf[o + 268:o + 284])
        value, vmin, vmax, _avg = struct.unpack_from("<dddd", buf, o + 284)
        readings.append({"type": rtype, "sensor_index": sidx, "id": rid, "label": label_user or label_orig,
                         "unit": unit, "value": value, "min": vmin, "max": vmax})
    return {"poll_time": poll, "sensors": sensors, "readings": readings}


def read_shared_memory() -> bytes:
    """Mở (chỉ đọc) bộ nhớ chia sẻ do HWiNFO tạo. Không tự tạo mới để khỏi làm hỏng HWiNFO."""
    if not sys.platform.startswith("win"):
        raise HwinfoUnavailable("HWiNFO chỉ có trên Windows.")
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenFileMappingW.restype = wintypes.HANDLE
    k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    FILE_MAP_READ = 0x0004
    handle = None
    for name in MAP_NAMES:
        handle = k32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if handle:
            break
    if not handle:
        raise HwinfoUnavailable("Chưa thấy HWiNFO: hãy mở HWiNFO (Sensors-only) và bật Shared Memory Support.")
    try:
        view = k32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
        if not view:
            raise HwinfoUnavailable("Không đọc được bộ nhớ chia sẻ của HWiNFO.")
        try:
            head = ctypes.string_at(view, HEADER.size)
            sig, _v, _r, _p, s_off, s_size, s_num, r_off, r_size, r_num = HEADER.unpack(head)
            if sig != SIG_ACTIVE:
                return head
            total = max(s_off + s_size * s_num, r_off + r_size * r_num)
            if total > 64 * 1024 * 1024:
                raise HwinfoUnavailable("Kích thước dữ liệu HWiNFO bất thường.")
            return ctypes.string_at(view, total)
        finally:
            k32.UnmapViewOfFile(view)
    finally:
        k32.CloseHandle(handle)


def temperatures(data: dict) -> list[dict]:
    """Chỉ lấy các giá trị nhiệt độ, kèm tên cảm biến (thiết bị) chứa nó."""
    sensors = data["sensors"]
    out = []
    for r in data["readings"]:
        if r["type"] != READING_TEMP or r["sensor_index"] >= len(sensors):
            continue
        s = sensors[r["sensor_index"]]
        value = r["value"]
        if "F" in r["unit"] and "C" not in r["unit"]:  # người dùng đặt HWiNFO hiển thị °F
            value = (value - 32) * 5 / 9
        out.append({"key": f"{s['id']:x}/{s['instance']}/{r['id']:x}", "label": r["label"],
                    "device": s["name"], "device_orig": s["name_orig"], "value": value})
    return out


def is_stale(data: dict, max_age: float = 60) -> bool:
    """HWiNFO bị treo/tạm dừng: lần cập nhật cuối quá cũ."""
    return data.get("poll_time", 0) > 0 and time.time() - data["poll_time"] > max_age


if __name__ == "__main__":  # kiểm tra nhanh: python hwinfo.py
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console Windows mặc định cp1252
    try:
        d = parse(read_shared_memory())
        for t in temperatures(d):
            print(f"{t['device']} | {t['label']} = {t['value']:.1f}°C")
    except HwinfoUnavailable as exc:  # không có HWiNFO là trạng thái hợp lệ
        print("HWiNFO không khả dụng:", exc)
