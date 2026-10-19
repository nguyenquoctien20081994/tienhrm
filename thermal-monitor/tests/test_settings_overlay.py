import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402


class ValidateSettings(unittest.TestCase):
    def test_accepts_valid_patch(self):
        patch = {"unit": "F", "thresholds": {"cpu": {"warn": 80, "crit": 92}},
                 "picks": {"board": "/nvme/0/temperature/0"}, "overlay": {"opacity": 0.7}}
        self.assertEqual(app.validate_settings_patch(patch), patch)

    def test_rejects_bad_values(self):
        bad = [
            {"unit": "K"},
            {"thresholds": {"cpu": {"warn": 95, "crit": 90}}},       # warn >= crit
            {"thresholds": {"cpu": {"warn": 10, "crit": 90}}},       # ngoài khoảng
            {"thresholds": {"ram": {"warn": 50, "crit": 60}}},       # khoá lạ
            {"thresholds": {"cpu": {"warn": True, "crit": 90}}},     # bool không phải số
            {"picks": {"cpu": 5}},
            {"overlay": {"opacity": 0.1}},
            ["not", "a", "dict"],
        ]
        for patch in bad:
            with self.assertRaises(ValueError, msg=patch):
                app.validate_settings_patch(patch)

    def test_unknown_top_level_keys_dropped(self):
        self.assertEqual(app.validate_settings_patch({"evil": 1, "unit": "C"}), {"unit": "C"})


class SettingsStoreTest(unittest.TestCase):
    def test_persist_merge_and_picks_replace(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            s = app.SettingsStore(path)
            s.update({"picks": {"cpu": "a", "gpu": "b"}})
            s.update({"picks": {"cpu": "c"}})        # quay gpu về AUTO
            s.update({"overlay": {"x": 10, "y": 20}})
            s.update({"overlay": {"opacity": 0.5}})  # không làm mất x/y
            reloaded = app.SettingsStore(path).get()
            self.assertEqual(reloaded["picks"], {"cpu": "c"})
            self.assertEqual(reloaded["overlay"], {"enabled": False, "opacity": 0.5, "x": 10, "y": 20})
            self.assertEqual(reloaded["thresholds"]["cpu"], {"warn": 85, "crit": 95})

    def test_switching_board_source_drops_manual_board_pick(self):
        s = app.SettingsStore(None)
        s.update({"picks": {"cpu": "c", "board": "/nvme/0/temperature/0"}})
        s.update({"board_source": "auto"})  # không đổi nguồn -> giữ lựa chọn
        self.assertEqual(s.get()["picks"]["board"], "/nvme/0/temperature/0")
        s.update({"board_source": "hwinfo"})
        self.assertEqual(s.get()["picks"], {"cpu": "c"})

    def test_corrupt_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                f.write("{broken")
            self.assertEqual(app.SettingsStore(path).get(), app.DEFAULT_SETTINGS)


try:
    import overlay
except ImportError:  # Pillow chưa cài
    overlay = None


@unittest.skipIf(overlay is None, "cần Pillow")
class OverlayRender(unittest.TestCase):
    TH = {"cpu": {"warn": 85, "crit": 95}, "gpu": {"warn": 85, "crit": 95}, "board": {"warn": 65, "crit": 80}}

    def test_render_sizes_scale_with_dpi(self):
        items = [{"disp": 71, "value": 71, "state": "live"}, {"disp": 18, "value": None, "state": "sleep"},
                 {"disp": 44.9, "value": 44.9, "state": "live", "substitute": True}]
        for scale in (1.0, 1.5):
            r = overlay.OverlayRenderer(scale, self.TH, "C")
            img = r.render(items, "live", 0.0)
            self.assertEqual(img.size, (r.w, r.h))
            self.assertEqual(img.mode, "RGBA")
        self.assertGreater(overlay.OverlayRenderer(1.5, self.TH, "C").w, overlay.OverlayRenderer(1.0, self.TH, "C").w)

    def test_rounded_corners_use_key_color(self):
        r = overlay.OverlayRenderer(1.0, self.TH, "C")
        img = r.render([{"disp": None, "value": None, "state": "stale"}] * 3, "off", 0.0)
        self.assertEqual(img.getpixel((0, 0))[:3], overlay.KEY_COLOR)  # góc ngoài trong suốt trên Windows
        self.assertNotEqual(img.getpixel((r.w // 2, r.h // 2))[:3], overlay.KEY_COLOR)

    def test_items_follow_picks_and_staleness(self):
        class Store:
            def get(self):
                return {"picks": {"board": "nvme"}, "thresholds": OverlayRender.TH, "unit": "C",
                        "overlay": {"opacity": 0.85}}
        ctl = overlay.OverlayController.__new__(overlay.OverlayController)
        ctl.settings = Store()
        snap = {"ts": 1000.0, "mode": "live", "primary": {"cpu": "pkg", "gpu": "igpu", "board": None},
                "sensors": [{"id": "pkg", "value": 61.0, "category": "cpu"},
                            {"id": "igpu", "value": None, "category": "gpu"},
                            {"id": "nvme", "value": 44.9, "category": "storage"}]}
        items, status = ctl._items(snap, 1001.0)
        self.assertEqual(status, "live")
        self.assertEqual([i["state"] for i in items], ["live", "sleep", "live"])
        self.assertTrue(items[2]["substitute"])
        items, status = ctl._items(snap, 1100.0)  # dữ liệu cũ hơn 6 giây
        self.assertEqual(status, "off")
        self.assertTrue(all(i["state"] == "stale" for i in items))


if __name__ == "__main__":
    unittest.main()
