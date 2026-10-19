import hashlib
import io
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import winsupport  # noqa: E402


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class InstallLhm(unittest.TestCase):
    def test_hash_mismatch_rejected(self):
        data = make_zip({"LibreHardwareMonitor.exe": b"x"})
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                winsupport.install_lhm_from_zip(data, os.path.join(d, "lhm"), "00" * 32)
            self.assertFalse(os.path.exists(os.path.join(d, "lhm")))

    def test_install_keeps_existing_config(self):
        data = make_zip({"LibreHardwareMonitor.exe": b"x", "HidSharp.dll": b"y"})
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "lhm")
            os.makedirs(dest)
            with open(os.path.join(dest, "LibreHardwareMonitor.config"), "w") as f:
                f.write("<configuration/>")
            winsupport.install_lhm_from_zip(data, dest, hashlib.sha256(data).hexdigest())
            self.assertTrue(os.path.isfile(os.path.join(dest, "HidSharp.dll")))
            self.assertTrue(os.path.isfile(os.path.join(dest, "LibreHardwareMonitor.config")))

    def test_missing_exe_rejected(self):
        data = make_zip({"readme.txt": b"x"})
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                winsupport.install_lhm_from_zip(data, os.path.join(d, "lhm"), hashlib.sha256(data).hexdigest())


class LhmConfig(unittest.TestCase):
    def test_create_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "LibreHardwareMonitor.config")
            self.assertFalse(winsupport.LhmManager._config_ok(path))
            with open(path, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="utf-8"?><configuration><appSettings>'
                        '<add key="theme" value="dark" /><add key="runWebServerMenuItem" value="false" />'
                        '</appSettings></configuration>')
            self.assertFalse(winsupport.LhmManager._config_ok(path))
            winsupport.write_lhm_config(path)
            values = {n.get("key"): n.get("value") for n in ET.parse(path).getroot().find("appSettings")}
            self.assertEqual(values["theme"], "dark")  # giữ cài đặt cũ của người dùng
            self.assertEqual(values["runWebServerMenuItem"], "true")
            self.assertEqual(values["listenerIp"], "127.0.0.1")
            self.assertEqual(sum(1 for _ in ET.parse(path).getroot().iter("add")), len(winsupport.LHM_SETTINGS) + 1)
            self.assertTrue(winsupport.LhmManager._config_ok(path))

    def test_corrupt_file_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "LibreHardwareMonitor.config")
            with open(path, "w") as f:
                f.write("not xml")
            winsupport.write_lhm_config(path)
            self.assertTrue(winsupport.LhmManager._config_ok(path))


if __name__ == "__main__":
    unittest.main()
