import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import assess  # noqa: E402
import sensors  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
SETTINGS = app.DEFAULT_SETTINGS


def snap_of(sensor_list, metrics=None, mode="live"):
    return sensors.snapshot(sensors.Reading(sensors=sensor_list, source="test", mode=mode, metrics=metrics or {}))


class ThinkCentreM910q(unittest.TestCase):
    """Lỗi thực tế: ổ cứng hiện 87°C đứng yên = ngưỡng cảnh báo của ổ bị hiểu nhầm là nhiệt độ."""

    def setUp(self):
        with open(os.path.join(FIX, "lhm_thinkcentre_m910q.json"), encoding="utf-8") as f:
            self.sensors, self.metrics = sensors.parse_lhm(json.load(f))

    def test_thresholds_are_not_temperatures(self):
        names = [s.name for s in self.sensors]
        self.assertNotIn("Warning Temperature", names)
        self.assertNotIn("Critical Temperature", names)
        self.assertFalse(any(s.value and s.value > 80 for s in self.sensors))

    def test_thresholds_attached_to_drive(self):
        nvme = next(s for s in self.sensors if s.id == "/nvme/0/temperature/0")
        self.assertEqual((nvme.value, nvme.warn, nvme.crit), (41.0, 86.8, 89.8))
        sata = next(s for s in self.sensors if s.id == "/hdd/1/temperature/0")
        self.assertEqual((sata.warn, sata.crit), (86.8, None))

    def test_board_falls_back_to_real_drive_temperature(self):
        p = sensors.pick_primary(self.sensors)
        self.assertEqual(p["board"], "/nvme/0/temperature/0")

    def test_metrics(self):
        m = self.metrics
        self.assertEqual(m["cpu_load"], 12.5)
        self.assertEqual(m["cpu_power"], 9.3)
        self.assertEqual(m["cpu_clock"], 2725)
        self.assertEqual(m["tjmax"], 100)  # 46 + 54

    def test_assessment_cool_idle(self):
        snap = snap_of(self.sensors, self.metrics)
        a = assess.evaluate(snap, SETTINGS)
        self.assertEqual(a["level"], "cool")
        self.assertIn("rất mát", a["headline"])
        self.assertIn("đang nghỉ", a["detail"])


def S(id_, name, cat, v, **kw):
    return sensors.Sensor(id_, name, "HW", cat, v, **kw)


class Rules(unittest.TestCase):
    def evaluate(self, cpu, load=None, history=None, extra=()):
        snap = snap_of([S("cpu", "CPU Package", "cpu", cpu), *extra], {"cpu_load": load} if load is not None else {})
        return assess.evaluate(snap, SETTINGS, history)

    def test_levels(self):
        self.assertEqual(self.evaluate(50)["level"], "cool")      # warn 85: còn 35°C
        self.assertEqual(self.evaluate(70)["level"], "normal")
        self.assertEqual(self.evaluate(80)["level"], "warm")
        self.assertEqual(self.evaluate(88)["level"], "hot")
        self.assertEqual(self.evaluate(97)["level"], "critical")

    def test_score_monotonic(self):
        scores = [self.evaluate(t)["score"] for t in (40, 60, 75, 85, 90, 99)]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(scores[0], 100)

    def test_hot_while_idle_means_poor_cooling(self):
        a = self.evaluate(82, load=5)
        self.assertIn("tản nhiệt có dấu hiệu kém", a["detail"])
        self.assertTrue(a["tips"])

    def test_cool_under_heavy_load_is_excellent(self):
        a = self.evaluate(55, load=90)
        self.assertIn("xuất sắc", a["headline"])

    def test_hot_under_load_is_explained(self):
        a = self.evaluate(90, load=95)
        self.assertEqual(a["level"], "hot")
        self.assertIn("bình thường khi tải cao", a["detail"])

    def test_rising_fast(self):
        hist = {"cpu": [(0.0, 50.0), (30.0, 54.0), (60.0, 58.0)]}
        self.assertIn("tăng nhanh", self.evaluate(58, history=hist)["detail"])

    def test_drive_uses_manufacturer_threshold(self):
        hot_drive = S("ssd", "Temperature", "storage", 80.0, warn=82.0, crit=85.0)
        a = self.evaluate(45, extra=[hot_drive])
        self.assertEqual(a["level"], "warm")  # còn < 10°C tới ngưỡng 82°C của ổ
        self.assertIn("Ổ", a["detail"])

    def test_no_sensors(self):
        self.assertEqual(assess.evaluate(snap_of([], mode="nosensor"), SETTINGS)["level"], "unknown")

    def test_igpu_estimate_not_double_counted(self):
        snap = snap_of([S("/intelcpu/0/t", "CPU Package", "cpu", 80.0)])
        self.assertEqual(snap["primary"]["gpu"], "derived/igpu")
        a = assess.evaluate(snap, SETTINGS)
        self.assertEqual(a["hottest"]["label"], "CPU")  # iGPU ước tính không bị tính thành linh kiện thứ hai
        self.assertNotIn("GPU", a["detail"])


class Interval(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(app.validate_settings_patch({"interval": 15}), {"interval": 15})
        for bad in (0, 1, 7, "15", 600):
            with self.assertRaises(ValueError):
                app.validate_settings_patch({"interval": bad})
        self.assertEqual(app.DEFAULT_SETTINGS["interval"], 15)


if __name__ == "__main__":
    unittest.main()
