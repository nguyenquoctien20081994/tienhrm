import os
import struct
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hwinfo  # noqa: E402
import sensors  # noqa: E402

S_SIZE, R_SIZE = 264 + 128, 316 + 144  # kích thước phần tử như HWiNFO bản mới (có thêm trường UTF-8)


def build_shm(devices, readings, sig=b"HWiS", poll=None):
    """devices: [tên]; readings: [(type, sensor_index, label, unit, value)] -> bytes đúng bố cục HWiNFO_SENS_SM2."""
    s_off = hwinfo.HEADER.size
    r_off = s_off + S_SIZE * len(devices)
    buf = bytearray(r_off + R_SIZE * len(readings))
    hwinfo.HEADER.pack_into(buf, 0, sig, 2, 2, int(poll or time.time()), s_off, S_SIZE, len(devices),
                            r_off, R_SIZE, len(readings))
    for i, name in enumerate(devices):
        o = s_off + i * S_SIZE
        struct.pack_into("<II", buf, o, 0xF000 + i, 0)
        buf[o + 8:o + 8 + len(name)] = name.encode("cp1252")
    for i, (rtype, sidx, label, unit, value) in enumerate(readings):
        o = r_off + i * R_SIZE
        struct.pack_into("<III", buf, o, rtype, sidx, 0x1000 + i)
        buf[o + 12:o + 12 + len(label)] = label.encode("cp1252")
        buf[o + 268:o + 268 + len(unit)] = unit.encode("cp1252")
        struct.pack_into("<dddd", buf, o + 284, value, value - 5, value + 5, value)
    return bytes(buf)


M910Q = build_shm(
    ["CPU [#0]: Intel Core i5-6500T: DTS", "LENOVO 310B (Nuvoton NCT6683D)", "S.M.A.R.T.: WDC PC SN730", "DIMM Temperature Sensor"],
    [(1, 0, "CPU Package", "°C", 44.0), (1, 1, "System", "°C", 46.0), (1, 1, "Temp2", "°C", 0.0),
     (3, 1, "CPU", "RPM", 1450.0), (1, 2, "Drive Temperature", "°C", 36.0), (1, 3, "DIMM[0] Temperature", "°C", 39.5)])


class Parse(unittest.TestCase):
    def test_layout(self):
        d = hwinfo.parse(M910Q)
        self.assertEqual(len(d["sensors"]), 4)
        self.assertEqual(d["sensors"][1]["name"], "LENOVO 310B (Nuvoton NCT6683D)")
        temps = hwinfo.temperatures(d)
        self.assertEqual([t["label"] for t in temps],
                         ["CPU Package", "System", "Temp2", "Drive Temperature", "DIMM[0] Temperature"])
        self.assertEqual(temps[1]["value"], 46.0)

    def test_disabled_and_garbage(self):
        with self.assertRaises(hwinfo.HwinfoUnavailable) as cm:
            hwinfo.parse(build_shm([], [], sig=b"DEAD"))
        self.assertIn("12 giờ", str(cm.exception))
        with self.assertRaises(hwinfo.HwinfoUnavailable):
            hwinfo.parse(b"\0" * 10)

    def test_fahrenheit_converted(self):
        d = hwinfo.parse(build_shm(["LENOVO 310B"], [(1, 0, "System", "°F", 113.0)]))
        self.assertAlmostEqual(hwinfo.temperatures(d)[0]["value"], 45.0)

    def test_stale(self):
        self.assertTrue(hwinfo.is_stale(hwinfo.parse(build_shm([], [], poll=time.time() - 600))))


class Categories(unittest.TestCase):
    def test_devices(self):
        cases = {"CPU [#0]: Intel Core i5-6500T: DTS": "cpu", "GPU [#0]: Intel HD Graphics 530": "gpu",
                 "S.M.A.R.T.: WDC PC SN730": "storage", "Drive: WDC PC SN730": "storage",
                 "LENOVO 310B (Nuvoton NCT6683D)": "board", "System: LENOVO 10MV": "board",
                 "ASUS PRIME B450M-A (Nuvoton NCT6798D)": "board", "DIMM Temperature Sensor": "other"}
        for dev, cat in cases.items():
            self.assertEqual(sensors.hwinfo_category(dev), cat, dev)


class SourceB(unittest.TestCase):
    """AutoProvider với nguồn mainboard B: dữ liệu LHM thật của M910q (không có SuperIO) + HWiNFO."""

    def provider(self, src):
        p = sensors.AutoProvider(None, board_source_fn=lambda: src)
        p.hwmon = p.acpi = p.cpu_load = None
        p.nvsmi.exe = None
        lhm = mock.Mock(url="x")
        lhm.name = "LibreHardwareMonitor"
        lhm.read.return_value = ([sensors.Sensor("/intelcpu/0/temperature/6", "CPU Package", "i5", "cpu", 44.0),
                                  sensors.Sensor("/nvme/0/temperature/0", "Composite Temperature", "WD", "storage", 36.0)],
                                 {"fans": []})
        p.lhm = lhm
        return p

    def test_b_uses_hwinfo_system_temp(self):
        with mock.patch("hwinfo.read_shared_memory", return_value=M910Q):
            snap = sensors.snapshot(self.provider("hwinfo").read())
        board = next(s for s in snap["sensors"] if s["id"] == snap["primary"]["board"])
        self.assertEqual((board["name"], board["value"]), ("System", 46.0))
        self.assertTrue(snap["metrics"]["board_source"]["active"])
        self.assertNotIn("Temp2", [s["name"] for s in snap["sensors"] if s["value"] is not None])  # 0°C = rác
        self.assertFalse(snap["messages"])

    def test_b_falls_back_to_a_when_hwinfo_missing(self):
        with mock.patch("hwinfo.read_shared_memory", side_effect=hwinfo.HwinfoUnavailable("Chưa thấy HWiNFO.")):
            snap = sensors.snapshot(self.provider("hwinfo").read())
        self.assertEqual(snap["primary"]["board"], "/nvme/0/temperature/0")
        self.assertFalse(snap["metrics"]["board_source"]["active"])
        self.assertTrue(any("Đang tạm dùng nguồn A" in m for m in snap["messages"]))

    def test_a_ignores_hwinfo(self):
        with mock.patch("hwinfo.read_shared_memory", return_value=M910Q) as rd:
            snap = sensors.snapshot(self.provider("auto").read())
        rd.assert_not_called()
        self.assertEqual(snap["primary"]["board"], "/nvme/0/temperature/0")


if __name__ == "__main__":
    unittest.main()
