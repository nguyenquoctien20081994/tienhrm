import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import assess  # noqa: E402
import history  # noqa: E402
import sensors  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
TH = app.DEFAULT_SETTINGS["thresholds"]


class HistoryDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = history.HistoryDB(os.path.join(self.dir.name, "h.db"))

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    def fill(self, now, hours=48, step=60, hot_hour=14):
        for ts in range(int(now - hours * 3600), int(now), step):
            h = time.localtime(ts).tm_hour
            cpu = 80.0 if h == hot_hour else 45.0
            self.db.conn.execute("INSERT INTO samples (ts, cpu, gpu, board, cpu_load, cpu_clock, throttle, level) "
                                 "VALUES (?, ?, NULL, 40, ?, 4000, 'none', 'cool')", (ts, cpu, 70 if h == hot_hour else 5))
        self.db.conn.commit()

    def test_report_finds_hottest_hour(self):
        now = time.time()
        self.fill(now)
        r = self.db.report(7, TH, now)
        self.assertEqual(r["hottest_hour"]["hour"], 14)
        self.assertEqual(r["components"]["cpu"]["max"], 80.0)
        self.assertNotIn("gpu", r["components"])  # cột rỗng (iGPU ước tính) không báo cáo
        self.assertTrue(any("14:00" in line for line in r["summary"]))
        self.assertEqual(r["step"], 60)

    def test_minutes_over_warn(self):
        now = time.time()
        self.fill(now)
        r = self.db.report(7, {**TH, "cpu": {"warn": 75, "crit": 95}}, now)
        self.assertEqual(r["components"]["cpu"]["minutes_over_warn"], 2 * 60)  # 1 giờ nóng × 2 ngày

    def test_series_buckets(self):
        now = time.time()
        self.fill(now, hours=24)
        s = self.db.series("24h", now)
        self.assertEqual(s["bucket"], 300)
        self.assertLessEqual(len(s["t"]), 290)
        self.assertEqual(max(v for v in s["cpu_max"] if v is not None), 80.0)
        self.assertEqual(len(self.db.series("1h", now)["t"]), 60)

    def test_record_and_prune(self):
        now = time.time()
        self.db.record(now - 40 * 86400, {"cpu": 50}, keep_days=30)  # lần ghi đầu dọn luôn dữ liệu cũ
        self.db.record(now, {"cpu": 51, "cpu_load": 60, "cpu_clock": 4100}, keep_days=30)
        n = self.db.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(self.db.peak_clock(), 4100)

    def test_empty_report(self):
        self.assertEqual(self.db.report(7, TH)["samples"], 0)

    def test_demo_seed_only_once(self):
        self.assertGreater(history.seed_demo(self.db, days=1, step=600), 0)
        self.assertEqual(history.seed_demo(self.db, days=1, step=600), 0)


class Throttle(unittest.TestCase):
    CPU_HOT = {"value": 97.0, "crit": 95}
    CPU_OK = {"value": 70.0, "crit": 95}

    def test_thermal(self):
        t = assess.throttle_state({"cpu_clock_max": 2600, "cpu_load": 90, "tjmax": 100}, self.CPU_HOT, 4400)
        self.assertEqual((t["state"], t["ratio"]), ("thermal", 59))

    def test_power_limit_only_at_full_load(self):
        m = {"cpu_clock_max": 2600, "cpu_load": 90}
        self.assertEqual(assess.throttle_state(m, self.CPU_OK, 4400)["state"], "power")
        m["cpu_load"] = 60  # chạy vừa: nhân nhanh nhất thấp chưa đủ kết luận
        self.assertEqual(assess.throttle_state(m, self.CPU_OK, 4400)["state"], "none")

    def test_idle_or_unknown(self):
        self.assertEqual(assess.throttle_state({"cpu_clock_max": 800, "cpu_load": 3}, self.CPU_OK, 4400)["state"], "none")
        self.assertIsNone(assess.throttle_state({"cpu_load": 90}, self.CPU_OK, 4400))
        self.assertIsNone(assess.throttle_state({"cpu_clock_max": 3000, "cpu_load": 90}, self.CPU_OK, None))

    def test_verdict_mentions_thermal_throttle(self):
        snap = sensors.snapshot(sensors.Reading(
            sensors=[sensors.Sensor("c", "CPU Package", "HW", "cpu", 84.0)], source="t",
            metrics={"cpu_clock_max": 2500, "cpu_load": 95, "tjmax": 88}))
        a = assess.evaluate(snap, app.DEFAULT_SETTINGS, peak_clock=4400)
        self.assertEqual(a["throttle"]["state"], "thermal")
        self.assertEqual(a["level"], "hot")  # hạ xung do nhiệt -> ít nhất là "nóng"
        self.assertIn("hạ xung", a["detail"])


class DriveHealth(unittest.TestCase):
    def test_parse_m910q(self):
        with open(os.path.join(FIX, "lhm_thinkcentre_m910q.json"), encoding="utf-8") as f:
            _, m = sensors.parse_lhm(json.load(f))
        d = m["drives"][0]
        self.assertEqual((d["life"], d["written_gb"], d["hours"], d["space_used"], d["temp"]),
                         (96.0, 12045.0, 8760.0, 72.4, 41.0))
        self.assertEqual(m["cores"][0], {"name": "CPU Core #1", "temp": 46.0, "load": 15.0, "clock": 2710})
        self.assertEqual(m["cpu_clock_max"], 2740)

    def test_worn_drive_verdict(self):
        snap = sensors.snapshot(sensors.Reading(
            sensors=[sensors.Sensor("c", "CPU Package", "HW", "cpu", 50.0)], source="t",
            metrics={"drives": [{"hardware": "SSD X", "life": 7.0}]}))
        a = assess.evaluate(snap, app.DEFAULT_SETTINGS)
        self.assertEqual(a["level"], "hot")
        self.assertEqual(a["headline"], "Nhiệt độ ổn – nhưng ổ cứng cần chú ý")
        self.assertTrue(any("SSD X" in t for t in a["tips"]))

    def test_spare_exhausted(self):
        notes, tips, worst = assess.drive_notes({"drives": [{"hardware": "N", "life": 80, "spare": 5, "spare_min": 10}]})
        self.assertEqual(worst, "hot")
        self.assertTrue(tips)


class RealM910qData(unittest.TestCase):
    """data.json thật từ ThinkCentre M910q (LHM 0.9.6, RawValue dạng chuỗi có đơn vị)."""

    def setUp(self):
        with open(os.path.join(FIX, "lhm_m910q_real_0.9.6.json"), encoding="utf-8") as f:
            self.sensors, self.m = sensors.parse_lhm(json.load(f))

    def test_core_loads_follow_core_order(self):
        # "Core Max"/"Core Average" đứng trước các nhân trong LHM -> không được làm lệch tải từng nhân
        self.assertEqual([c["name"] for c in self.m["cores"]], [f"CPU Core #{i}" for i in range(1, 5)])
        self.assertEqual([c["load"] for c in self.m["cores"]], [56.3, 53.1, 51.6, 48.4])
        self.assertEqual([c["temp"] for c in self.m["cores"]], [43.0, 44.0, 42.0, 43.0])

    def test_values_parsed_from_string_raw(self):
        by = {s.id: s for s in self.sensors}
        self.assertEqual(by["/intelcpu/0/temperature/6"].value, 44.0)
        nvme = by["/nvme/0/temperature/0"]
        self.assertEqual((nvme.value, nvme.warn, nvme.crit), (36.0, 83.0, 87.0))
        self.assertEqual((self.m["tjmax"], self.m["cpu_load"], self.m["drives"][0]["life"]), (100, 52.3, 98.0))

    def test_no_superio_board_uses_ssd(self):
        self.assertFalse(any(s.category == "board" for s in self.sensors))
        self.assertEqual(sensors.pick_primary(self.sensors)["board"], "/nvme/0/temperature/0")


class NewSettings(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(app.validate_settings_patch({"history_days": 14, "ui": {"style": "cockpit", "theme": "day"}}),
                         {"history_days": 14, "ui": {"style": "cockpit", "theme": "day"}})
        for bad in ({"history_days": 3}, {"ui": {"style": "retro"}}, {"ui": {"theme": "dark"}}, {"ui": "hud"}):
            with self.assertRaises(ValueError):
                app.validate_settings_patch(bad)


if __name__ == "__main__":
    unittest.main()
