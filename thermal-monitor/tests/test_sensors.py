import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sensors  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class ParseValue(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(sensors.parse_lhm_value("45.0 °C"), 45.0)
        self.assertEqual(sensors.parse_lhm_value("45,5 °C"), 45.5)
        self.assertAlmostEqual(sensors.parse_lhm_value("104.0 °F"), 40.0)
        self.assertIsNone(sensors.parse_lhm_value("35,2 %"))
        self.assertIsNone(sensors.parse_lhm_value(""))
        self.assertIsNone(sensors.parse_lhm_value(None))


class LhmLaptop(unittest.TestCase):
    def setUp(self):
        self.list = sensors.parse_lhm_tree(load("lhm_laptop.json"))
        self.by_id = {s.id: s for s in self.list}

    def test_only_temperatures(self):
        self.assertEqual(len(self.list), 6)  # 3 CPU + 2 GPU + 1 NVMe, không lấy Load
        self.assertTrue(all(s.value is not None for s in self.list))

    def test_categories(self):
        self.assertEqual(self.by_id["/intelcpu/0/temperature/14"].category, "cpu")
        self.assertEqual(self.by_id["/gpu-nvidia/0/temperature/0"].category, "gpu")
        self.assertEqual(self.by_id["/nvme/0/temperature/0"].category, "storage")
        pkg = self.by_id["/intelcpu/0/temperature/14"]
        self.assertEqual((pkg.value, pkg.min, pkg.max), (71.0, 47.0, 93.0))
        self.assertEqual(pkg.hardware, "12th Gen Intel Core i7-12700H")

    def test_primary(self):
        p = sensors.pick_primary(self.list)
        self.assertEqual(p["cpu"], "/intelcpu/0/temperature/14")  # CPU Package
        self.assertEqual(p["gpu"], "/gpu-nvidia/0/temperature/0")  # GPU Core, không phải Hot Spot
        self.assertEqual(p["board"], "/nvme/0/temperature/0")  # không có cảm biến mainboard -> SSD thay thế


class ThinkPadX1YogaGen5(unittest.TestCase):
    """i7-10610U + Intel UHD (iGPU) + NVMe, không có chip SuperIO."""

    def setUp(self):
        self.list = sensors.parse_lhm_tree(load("lhm_thinkpad_x1_yoga_g5.json"))
        self.snap = sensors.snapshot(sensors.Reading(sensors=self.list, source="LHM"))
        self.by_id = {s["id"]: s for s in self.snap["sensors"]}

    def test_distance_to_tjmax_excluded(self):
        self.assertFalse(any("Distance" in s.name for s in self.list))

    def test_raw_value_used(self):
        self.assertEqual(self.by_id["/nvme/0/temperature/0"]["value"], 44.9)  # RawValue 44.85, làm tròn 1 số

    def test_primary_and_estimates(self):
        p = self.snap["primary"]
        self.assertEqual(self.by_id[p["cpu"]]["name"], "CPU Package")
        gpu = self.by_id[p["gpu"]]
        self.assertEqual(gpu["id"], "derived/igpu")
        self.assertEqual(gpu["value"], 61.0)
        self.assertIn("ước tính", gpu["hardware"])
        self.assertEqual(p["board"], "/nvme/0/temperature/0")


class LhmDesktopOld(unittest.TestCase):
    def setUp(self):
        self.list = sensors.parse_lhm_tree(load("lhm_desktop_old.json"))

    def test_superio_and_fahrenheit(self):
        names = {s.name: s for s in self.list}
        self.assertEqual(names["Motherboard"].category, "board")
        self.assertEqual(names["Motherboard"].hardware, "MSI MAG B550 TOMAHAWK / Nuvoton NCT6797D")
        self.assertAlmostEqual(names["Temperature #3"].value, 40.0)
        self.assertEqual(names["Core (Tctl/Tdie)"].category, "cpu")
        self.assertEqual(names["GPU Core"].category, "gpu")
        self.assertNotIn("Fan #1", names)

    def test_primary(self):
        p = sensors.pick_primary(self.list)
        by_id = {s.id: s for s in self.list}
        self.assertEqual(by_id[p["cpu"]].name, "Core (Tctl/Tdie)")
        self.assertEqual(by_id[p["gpu"]].name, "GPU Core")
        self.assertEqual(by_id[p["board"]].name, "Motherboard")


class RyzenWithRadeonName(unittest.TestCase):
    def test_apu_cpu_not_gpu(self):
        tree = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
            {"Text": "AMD Ryzen 7 5800H with Radeon Graphics", "ImageURL": "images_icon/cpu.png", "Children": [
                {"Text": "Temperatures", "ImageURL": "images_icon/temperature.png", "Children": [
                    {"Text": "Core (Tctl/Tdie)", "Value": "60.0 °C", "Children": []}]}]}]}]}
        (s,) = sensors.parse_lhm_tree(tree)
        self.assertEqual(s.category, "cpu")


class GpuSleep(unittest.TestCase):
    def test_discrete_gpu_asleep_kept(self):
        lst = [sensors.Sensor("a", "GPU Core", "NVIDIA GeForce RTX 3060", "gpu", None),
               sensors.Sensor("b", "GPU Temperature", "Intel Iris Xe", "gpu", 50.0)]
        self.assertEqual(sensors.pick_primary(lst)["gpu"], "a")


class NvidiaSmi(unittest.TestCase):
    def test_parse(self):
        out = "0, NVIDIA GeForce RTX 3060 Laptop GPU, 54\n1, NVIDIA T600, [N/A]\n"
        lst = sensors.parse_nvidia_smi(out)
        self.assertEqual([s.value for s in lst], [54.0, None])
        self.assertEqual(lst[0].hardware, "NVIDIA GeForce RTX 3060 Laptop GPU")


class Hwmon(unittest.TestCase):
    def test_read(self):
        with tempfile.TemporaryDirectory() as root:
            def put(path, text):
                with open(path, "w") as f:
                    f.write(text)

            def chip(n, name, temps):
                d = os.path.join(root, f"hwmon{n}")
                os.makedirs(d)
                put(os.path.join(d, "name"), name + "\n")
                for i, (label, milli) in enumerate(temps, 1):
                    put(os.path.join(d, f"temp{i}_input"), str(milli))
                    if label:
                        put(os.path.join(d, f"temp{i}_label"), label)
            chip(0, "acpitz", [(None, 45000)])
            chip(1, "coretemp", [("Package id 0", 61000), ("Core 0", 58000)])
            chip(2, "amdgpu", [("edge", 50000)])
            chip(3, "nvme", [("Composite", 39850)])
            lst = sensors.LinuxHwmonProvider(root).read()
            cats = {s.name: s.category for s in lst}
            self.assertEqual(cats, {"temp1": "board", "Package id 0": "cpu", "Core 0": "cpu",
                                    "edge": "gpu", "Composite": "storage"})
            p = sensors.pick_primary(lst)
            by_id = {s.id: s for s in lst}
            self.assertEqual(by_id[p["cpu"]].name, "Package id 0")
            self.assertEqual(by_id[p["board"]].value, 45.0)


class Snapshot(unittest.TestCase):
    def test_demo_snapshot_is_json(self):
        snap = sensors.snapshot(sensors.DemoAuto().read())
        json.dumps(snap)
        self.assertEqual(snap["mode"], "demo")
        self.assertTrue(all(snap["primary"][k] for k in ("cpu", "gpu", "board")))


if __name__ == "__main__":
    unittest.main()
