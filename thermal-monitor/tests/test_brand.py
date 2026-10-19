import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class BrandSettings(unittest.TestCase):
    def test_brand_validated(self):
        self.assertEqual(app.validate_settings_patch({"ui": {"brand": "viettam"}}), {"ui": {"brand": "viettam"}})
        with self.assertRaises(ValueError):
            app.validate_settings_patch({"ui": {"brand": "khac"}})

    def test_default_and_old_settings_keep_brand(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w", encoding="utf-8") as f:  # settings.json của bản cũ: ui chưa có brand
                json.dump({"settings_version": 3, "ui": {"style": "cockpit", "theme": "day"}}, f)
            ui = app.SettingsStore(path).get()["ui"]
            self.assertEqual(ui, {"style": "cockpit", "theme": "day", "brand": "thermal"})

    def test_image_type_by_magic(self):
        self.assertEqual(app.image_type(PNG), "image/png")
        self.assertEqual(app.image_type(b"\xff\xd8\xff\xe0" + b"\0" * 8), "image/jpeg")
        self.assertEqual(app.image_type(b"RIFF\0\0\0\0WEBPVP8 "), "image/webp")
        self.assertIsNone(app.image_type(b"<svg onload='alert(1)'>"))


class BrandEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        brand = app.BrandStore(os.path.join(self.tmp.name, "brand"))
        handler = app.make_handler(mock.Mock(), self.tmp.name, app.SettingsStore(None), brand=brand)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, r.getheader("Content-Type"), data

    def test_upload_serve_delete(self):
        self.assertEqual(json.loads(self.req("GET", "/api/brand")[2]), {"nam": False, "nu": False, "logo": False})
        self.assertEqual(self.req("GET", "/api/brand/nam")[0], 404)
        st, _, body = self.req("POST", "/api/brand/nam", PNG, {"X-TCH": "1", "Content-Type": "image/png"})
        self.assertEqual((st, json.loads(body)["nam"]), (200, True))
        self.assertEqual(self.req("GET", "/api/brand/nam"), (200, "image/png", PNG))
        st, _, body = self.req("DELETE", "/api/brand/nam", headers={"X-TCH": "1"})
        self.assertEqual((st, json.loads(body)["nam"]), (200, False))

    def test_rejects_bad_requests(self):
        h = {"X-TCH": "1"}
        self.assertEqual(self.req("POST", "/api/brand/nam", PNG)[0], 403)  # thiếu header chống CSRF
        self.assertEqual(self.req("POST", "/api/brand/nam", b"<svg/>", h)[0], 400)  # không phải ảnh bitmap
        self.assertEqual(self.req("POST", "/api/brand/../settings", PNG, h)[0], 404)
        self.assertEqual(self.req("POST", "/api/brand/khac", PNG, h)[0], 404)
        big = b"\x89PNG\r\n\x1a\n" + b"\0" * app.BRAND_MAX_BYTES
        try:  # server từ chối ngay, không đọc hết thân -> client có thể bị ngắt khi đang gửi
            self.assertEqual(self.req("POST", "/api/brand/nu", big, h)[0], 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.assertFalse(any(json.loads(self.req("GET", "/api/brand")[2]).values()))

    def test_info_has_author(self):
        self.assertEqual(json.loads(self.req("GET", "/api/info")[2])["author"], "Nguyễn Quốc Tiến")


if __name__ == "__main__":
    unittest.main()
