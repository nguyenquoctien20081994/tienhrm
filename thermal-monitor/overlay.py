"""Overlay nổi: 3 đồng hồ nhỏ ở góc trên bên phải màn hình, trong suốt mờ, luôn nằm trên các app khác.

- Cửa sổ Tk không viền (overrideredirect), -topmost, -alpha; Windows: bo góc bằng -transparentcolor.
- Mặt đồng hồ vẽ bằng Pillow, vẽ ở độ phân giải cao rồi thu nhỏ (khử răng cưa).
- Chỉ vẽ lại khi kim/giá trị thay đổi -> gần như không tốn CPU khi nhiệt độ ổn định.
- Thao tác: kéo thả để di chuyển, cuộn chuột đổi độ trong suốt, nhấp đúp mở bảng điều khiển,
  chuột phải mở menu.

Mọi lệnh Tk chạy trên luồng chính; luồng khác gửi yêu cầu qua `OverlayController.request()`.
"""

from __future__ import annotations

import math
import os
import queue
import sys
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

IS_WINDOWS = sys.platform.startswith("win")
KEY_COLOR = (1, 2, 3)  # màu "trong suốt hoàn toàn" ngoài góc bo (Windows -transparentcolor)
SS = 3                 # hệ số vẽ phóng to cho phần tĩnh
SS_DYN = 2             # hệ số cho kim (vẽ mỗi khung hình)

GAUGES = [
    {"key": "cpu", "label": "CPU", "accent": (34, 229, 255)},
    {"key": "gpu", "label": "GPU", "accent": (255, 61, 184)},
    {"key": "board", "label": "BOARD", "accent": (61, 255, 162)},
]
SCALE_RANGE = {"cpu": (20, 110), "gpu": (20, 110), "board": (20, 100)}
AMBER, RED, MUTED, TEXT = (255, 176, 32), (255, 59, 78), (110, 134, 163), (211, 230, 251)
START_DEG, SWEEP_DEG = 135.0, 270.0


def _font(size: int, mono: bool = False):
    names = (["consolab.ttf", "consola.ttf", "CascadiaMono.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSansMono.ttf"]
             if mono else ["bahnschrift.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _c2f(c):
    return c * 9 / 5 + 32


class GaugeRenderer:
    """Vẽ một đồng hồ đường kính `d` px (đã nhân DPI)."""

    def __init__(self, cfg: dict, d: int, thresholds: dict, unit: str):
        self.cfg, self.d, self.unit = cfg, d, unit
        self.lo, self.hi = SCALE_RANGE[cfg["key"]]
        self.warn, self.crit = thresholds["warn"], thresholds["crit"]
        self.static = self._build_static()

    def frac(self, c: float) -> float:
        return max(-0.02, min(1.02, (c - self.lo) / (self.hi - self.lo)))

    def angle(self, c: float) -> float:
        return START_DEG + self.frac(c) * SWEEP_DEG

    def zone_color(self, c):
        return RED if c >= self.crit else AMBER if c >= self.warn else self.cfg["accent"]

    def _build_static(self) -> Image.Image:
        S = self.d * SS
        R = S / 2
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        acc = self.cfg["accent"]

        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([R * 0.03, R * 0.03, S - R * 0.03, S - R * 0.03], fill=acc + (110,))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(R * 0.05)))
        dr = ImageDraw.Draw(img)

        # Vành thép: dải màu xoay quanh tâm (giả gradient hình nón)
        stops = [(0, (59, 70, 86)), (45, (199, 210, 223)), (90, (85, 97, 111)), (145, (154, 167, 182)),
                 (200, (43, 52, 63)), (250, (213, 222, 233)), (305, (74, 86, 100)), (360, (59, 70, 86))]
        box = [R * 0.06, R * 0.06, S - R * 0.06, S - R * 0.06]
        for a in range(0, 360, 2):
            for (a0, c0), (a1, c1) in zip(stops, stops[1:]):
                if a0 <= a < a1:
                    col = _mix(c0, c1, (a - a0) / (a1 - a0))
                    break
            dr.pieslice(box, a - 45, a - 43, fill=col + (255,))
        for i in range(90):  # khía vành
            a = math.radians(i * 4)
            dr.line([(R + math.cos(a) * R * 0.86, R + math.sin(a) * R * 0.86),
                     (R + math.cos(a) * R * 0.93, R + math.sin(a) * R * 0.93)], fill=(0, 0, 0, 90), width=max(1, SS))
        # Mặt số (gradient tròn)
        steps = 28
        for i in range(steps):
            t = i / (steps - 1)
            r = R * 0.84 * (1 - t)
            col = _mix((2, 5, 10), (17, 34, 56), t ** 1.4)
            dr.ellipse([R - r, R - r * 1.0 - R * 0.08 * t, R + r, R + r - R * 0.08 * t], fill=col + (255,))
        dr.ellipse([R * 0.16, R * 0.16, S - R * 0.16, S - R * 0.16], outline=(17, 22, 29, 255), width=int(R * 0.03))

        # Dải vùng nhiệt (mờ)
        ar = R * 0.74
        abox = [R - ar, R - ar, R + ar, R + ar]
        face = (8, 16, 28)
        for a, z, col in [(self.lo, self.warn, acc), (self.warn, self.crit, AMBER), (self.crit, self.hi, RED)]:
            if z > a:
                dr.arc(abox, self.angle(a), self.angle(z), fill=_mix(col, face, 0.65) + (255,), width=int(R * 0.07))

        # Vạch chia & số
        f_num = _font(int(R * 0.17))
        for v in range(self.lo, self.hi + 1, 5):
            major = v % 10 == 0
            a = math.radians(self.angle(v))
            r1, r2 = R * 0.66, R * (0.55 if major else 0.6)
            col = (RED if v >= self.crit else AMBER if v >= self.warn else (227, 238, 250)) if major else (95, 117, 144)
            dr.line([(R + math.cos(a) * r1, R + math.sin(a) * r1), (R + math.cos(a) * r2, R + math.sin(a) * r2)],
                    fill=col + (255,), width=int(R * (0.03 if major else 0.014)))
            if v % 20 == 0 and math.sin(a) < 0.45:  # bỏ số ở đáy để không đè lên ô số điện tử
                label = str(round(_c2f(v))) if self.unit == "F" else str(v)
                dr.text((R + math.cos(a) * R * 0.42, R + math.sin(a) * R * 0.42), label,
                        font=f_num, fill=(201, 217, 234, 255), anchor="mm")

        # Ô số điện tử
        w, h, y = R * 0.78, R * 0.3, R * 1.4
        dr.rounded_rectangle([R - w / 2, y - h / 2, R + w / 2, y + h / 2], radius=R * 0.06,
                             fill=(0, 0, 0, 200), outline=(201, 168, 106, 230), width=max(1, int(R * 0.015)))
        return img.resize((self.d, self.d), Image.LANCZOS)

    def render(self, disp: float | None, value: float | None, state: str, pulse: float) -> Image.Image:
        """disp: vị trí kim (°C, có quán tính); value: giá trị thật; state: live|sleep|nosensor|stale."""
        S = self.d * SS_DYN
        R = S / 2
        layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        live = state == "live" and disp is not None
        pos = disp if live else self.lo - 2

        if live:  # dải sáng theo giá trị
            ar = R * 0.74
            abox = [R - ar, R - ar, R + ar, R + ar]
            for a, z, col in [(self.lo, min(pos, self.warn), self.cfg["accent"]),
                              (self.warn, min(pos, self.crit), AMBER), (self.crit, pos, RED)]:
                if z > a:
                    dr.arc(abox, self.angle(a), self.angle(z), fill=col + (255,), width=int(R * 0.07))
            if value is not None and value >= self.crit:
                dr.ellipse([R * 0.12, R * 0.12, S - R * 0.12, S - R * 0.12],
                           outline=RED + (int(90 + 120 * pulse),), width=int(R * 0.04))

        # Kim
        a = math.radians(self.angle(pos))
        ca, sa = math.cos(a), math.sin(a)
        px, py = -sa, ca

        def pt(along, across):
            return (R + ca * R * along + px * R * across, R + sa * R * along + py * R * across)

        needle = (255, 98, 60) if live else (85, 96, 110)
        dr.polygon([pt(0.7, 0), pt(0.06, -0.035), pt(-0.18, -0.025), pt(-0.18, 0.025), pt(0.06, 0.035)],
                   fill=needle + (255,))
        cx, cy = pt(-0.14, 0)
        dr.ellipse([cx - R * 0.05, cy - R * 0.05, cx + R * 0.05, cy + R * 0.05], fill=(179, 23, 12, 255) if live else (44, 51, 60, 255))
        # Trục đồng + chân kính ruby
        for rr, col in [(0.11, (92, 71, 36)), (0.085, (201, 168, 106)), (0.05, (26, 18, 8)), (0.028, (200, 20, 50))]:
            dr.ellipse([R - R * rr, R - R * rr, R + R * rr, R + R * rr], fill=col + (255,))
        return layer.resize((self.d, self.d), Image.LANCZOS)


class OverlayRenderer:
    def __init__(self, scale: float, thresholds: dict, unit: str):
        self.scale = scale
        self.unit = unit
        s = lambda v: int(round(v * scale))  # noqa: E731
        self.d, self.pad, self.top, self.bottom = s(118), s(10), s(22), s(20)
        self.w = self.pad + 3 * (self.d + self.pad)
        self.h = self.top + self.d + self.bottom + s(4)
        self.gauges = [GaugeRenderer(g, self.d, thresholds[g["key"]], unit) for g in GAUGES]
        self.f_title = _font(s(10))
        self.f_label = _font(s(10))
        self.f_value = _font(s(17), mono=True)
        self.f_small = _font(s(9), mono=True)
        self.close_box = (self.w - s(22), s(3), self.w - s(5), s(19))
        self.base = self._build_base()

    def gauge_origin(self, i: int) -> tuple[int, int]:
        return self.pad + i * (self.d + self.pad), self.top

    def _build_base(self) -> Image.Image:
        s = self.scale
        img = Image.new("RGBA", (self.w, self.h), KEY_COLOR + (255,))
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle([0, 0, self.w - 1, self.h - 1], radius=int(14 * s), fill=(6, 12, 22, 255),
                             outline=(34, 229, 255, 255), width=max(1, int(s)))
        # Góc HUD
        L, t = int(12 * s), max(2, int(2 * s))
        for x0, y0, dx, dy in [(5, 5, 1, 1), (self.w - 6, 5, -1, 1), (5, self.h - 6, 1, -1), (self.w - 6, self.h - 6, -1, -1)]:
            dr.line([(x0, y0), (x0 + dx * L, y0)], fill=(34, 229, 255, 255), width=t)
            dr.line([(x0, y0), (x0, y0 + dy * L)], fill=(34, 229, 255, 255), width=t)
        dr.text((int(14 * s), int(11 * s)), "THERMAL CORE", font=self.f_title, fill=(150, 175, 200, 255), anchor="lm")
        self.status_x = int(14 * s + dr.textlength("THERMAL CORE", font=self.f_title) + 10 * s)
        x0, y0, x1, y1 = self.close_box
        dr.line([(x0 + 5 * s, y0 + 5 * s), (x1 - 5 * s, y1 - 5 * s)], fill=(150, 175, 200, 255), width=max(1, int(1.5 * s)))
        dr.line([(x0 + 5 * s, y1 - 5 * s), (x1 - 5 * s, y0 + 5 * s)], fill=(150, 175, 200, 255), width=max(1, int(1.5 * s)))
        for i, g in enumerate(self.gauges):
            ox, oy = self.gauge_origin(i)
            img.alpha_composite(g.static, (ox, oy))
        return img

    def render(self, items: list[dict], status: str, now: float) -> Image.Image:
        """items[i]: {disp, value, state, name, substitute}; status: live|demo|off."""
        img = self.base.copy()
        dr = ImageDraw.Draw(img)
        s = self.scale
        pulse = 0.5 + 0.5 * math.sin(now * 7)
        dot = {"live": (61, 255, 162), "demo": (255, 61, 184)}.get(status, RED)
        if status != "live" or int(now * 2) % 2 == 0:
            r = 3.2 * s
            cx, cy = self.status_x, int(11 * s)
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot + (255,))
        dr.text((self.status_x + int(7 * s), int(11 * s)), {"live": "LIVE", "demo": "DEMO"}.get(status, "NO SIGNAL"),
                font=self.f_small, fill=dot + (255,), anchor="lm")

        for i, (g, it) in enumerate(zip(self.gauges, items)):
            ox, oy = self.gauge_origin(i)
            img.alpha_composite(g.render(it["disp"], it["value"], it["state"], pulse), (ox, oy))
            cx, wy = ox + self.d / 2, oy + self.d * 0.7
            if it["state"] == "live" and it["value"] is not None:
                v = _c2f(it["value"]) if self.unit == "F" else it["value"]
                dr.text((cx, wy), f"{v:.1f}", font=self.f_value, fill=g.zone_color(it["value"]) + (255,), anchor="mm")
            else:
                label = {"sleep": "SLEEP", "nosensor": "N/A", "stale": "NO SIG"}.get(it["state"], "--")
                dr.text((cx, wy), label, font=self.f_small, fill=MUTED + (255,), anchor="mm")
            name = g.cfg["label"] + ("*" if it.get("substitute") else "")
            dr.text((cx, oy + self.d + 9 * s), name, font=self.f_label, fill=g.cfg["accent"] + (255,), anchor="mm")
        return img


# --------------------------------------------------------------------------- cửa sổ Tk

class OverlayController:
    def __init__(self, root, get_snapshot, settings, open_main, quit_app):
        self.root = root
        self.get_snapshot = get_snapshot
        self.settings = settings          # SettingsStore (thread-safe)
        self.open_main = open_main
        self.quit_app = quit_app
        self.queue: queue.Queue = queue.Queue()
        self.win = None
        self.visible = False
        self.ever_shown = False
        self.renderer = None
        self.render_key = None
        self.anim = {}
        self.last_topmost = 0.0
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.root.after(100, self._pump)

    # ---- gọi từ luồng bất kỳ
    def request(self, action: str) -> None:
        self.queue.put(action)

    # ---- luồng Tk
    def _pump(self) -> None:
        try:
            while True:
                action = self.queue.get_nowait()
                if action == "show":
                    self.show()
                elif action == "hide":
                    self.hide()
                elif action == "toggle":
                    self.hide() if self.visible else self.show()
                elif action == "reload":  # cài đặt đổi: _tick tự dựng lại khi ngưỡng/đơn vị khác
                    if self.win is not None:
                        self.win.attributes("-alpha", self.settings.get()["overlay"]["opacity"])
                elif action == "quit":
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _settings_key(self):
        cfg = self.settings.get()
        th = cfg["thresholds"]
        return (cfg["unit"], tuple((k, th[k]["warn"], th[k]["crit"]) for k in sorted(th)), self.scale)

    def show(self) -> None:
        import tkinter as tk

        if self.win is None:
            self.win = tk.Toplevel(self.root)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            if IS_WINDOWS:
                self.win.attributes("-toolwindow", True)
                self.win.configure(bg="#%02x%02x%02x" % KEY_COLOR)
                self.win.attributes("-transparentcolor", "#%02x%02x%02x" % KEY_COLOR)
            self.label = tk.Label(self.win, bd=0, highlightthickness=0, bg="#%02x%02x%02x" % KEY_COLOR)
            self.label.pack()
            self._bind()
            self._ensure_renderer()
            self.photo = None
            self._place()
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", self.settings.get()["overlay"]["opacity"])
        self.visible = True
        self.ever_shown = True
        self.settings.update({"overlay": {"enabled": True}})
        self._tick()

    def hide(self) -> None:
        if self.win is not None:
            self.win.withdraw()
        self.visible = False
        self.settings.update({"overlay": {"enabled": False}})

    def _ensure_renderer(self) -> None:
        key = self._settings_key()
        if key != self.render_key:
            cfg = self.settings.get()
            self.renderer = OverlayRenderer(self.scale, cfg["thresholds"], cfg["unit"])
            self.render_key = key

    def _place(self) -> None:
        r = self.renderer
        ov = self.settings.get()["overlay"]
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        margin = int(12 * self.scale)
        x, y = ov.get("x"), ov.get("y")
        if x is None or y is None or not (0 <= x <= sw - 40 and 0 <= y <= sh - 40):
            x, y = sw - r.w - margin, margin  # góc trên bên phải
        self.win.geometry(f"{r.w}x{r.h}+{int(x)}+{int(y)}")

    def _bind(self) -> None:
        import tkinter as tk

        w = self.label
        w.bind("<ButtonPress-1>", self._press)
        w.bind("<B1-Motion>", self._drag)
        w.bind("<ButtonRelease-1>", self._release)
        w.bind("<Double-Button-1>", lambda e: self.open_main())
        w.bind("<Button-3>", self._menu)
        w.bind("<MouseWheel>", lambda e: self._opacity_step(1 if e.delta > 0 else -1))
        w.bind("<Button-4>", lambda e: self._opacity_step(1))
        w.bind("<Button-5>", lambda e: self._opacity_step(-1))
        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_command(label="Mở bảng điều khiển", command=self.open_main)
        op = tk.Menu(self.menu, tearoff=0)
        for pct in (100, 90, 80, 70, 60, 50, 40):
            op.add_command(label=f"{pct}%", command=lambda p=pct: self._set_opacity(p / 100))
        self.menu.add_cascade(label="Độ trong suốt", menu=op)
        self.menu.add_command(label="Về góc trên bên phải", command=self._reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="Ẩn overlay", command=self.hide)
        self.menu.add_command(label="Thoát Thermal Core", command=self.quit_app)

    def _press(self, e) -> None:
        x0, y0, x1, y1 = self.renderer.close_box
        if x0 <= e.x <= x1 and y0 <= e.y <= y1:
            self._drag_from = None
            self.hide()
            return
        self._drag_from = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag(self, e) -> None:
        if getattr(self, "_drag_from", None):
            dx, dy = self._drag_from
            self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _release(self, e) -> None:
        if getattr(self, "_drag_from", None):
            self.settings.update({"overlay": {"x": self.win.winfo_x(), "y": self.win.winfo_y()}})
        self._drag_from = None

    def _menu(self, e) -> None:
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _set_opacity(self, value: float) -> None:
        value = max(0.25, min(1.0, value))
        self.win.attributes("-alpha", value)
        self.settings.update({"overlay": {"opacity": round(value, 2)}})

    def _opacity_step(self, direction: int) -> None:
        self._set_opacity(self.settings.get()["overlay"]["opacity"] + 0.05 * direction)

    def _reset_position(self) -> None:
        self.settings.update({"overlay": {"x": None, "y": None}})
        self._place()

    # ---- vòng vẽ
    def _items(self, snap: dict | None, now_wall: float) -> tuple[list[dict], str]:
        cfg = self.settings.get()
        sensors = (snap or {}).get("sensors", [])
        by_id = {s["id"]: s for s in sensors}
        stale = snap is None or now_wall - snap.get("ts", 0) > 6
        status = "off" if stale else ("demo" if snap.get("mode") == "demo" else "live" if sensors else "off")
        items = []
        for g in GAUGES:
            pick = cfg["picks"].get(g["key"], "auto")
            s = by_id.get(pick) if pick != "auto" else None
            if s is None:
                s = by_id.get((snap or {}).get("primary", {}).get(g["key"]))
            if stale:
                state = "stale"
            elif s is None:
                state = "nosensor"
            elif s["value"] is None:
                state = "sleep"
            else:
                state = "live"
            items.append({"value": s["value"] if s else None, "state": state,
                          "substitute": bool(s and s["category"] != g["key"])})
        return items, status

    def _tick(self) -> None:
        if not self.visible:
            return
        self._ensure_renderer()
        now = time.monotonic()
        dt = min(0.1, now - getattr(self, "_last_tick", now)) or 0.04
        self._last_tick = now
        items, status = self._items(self.get_snapshot(), time.time())
        moving = False
        for g, it in zip(GAUGES, items):
            a = self.anim.setdefault(g["key"], {"pos": 18.0, "vel": 0.0})
            target = it["value"] if it["state"] == "live" else 18.0
            a["vel"] += ((target - a["pos"]) * 70 - a["vel"] * 12) * dt
            a["pos"] += a["vel"] * dt
            if abs(target - a["pos"]) > 0.02 or abs(a["vel"]) > 0.05:
                moving = True
            it["disp"] = a["pos"]
        alarm = any(it["state"] == "live" and it["value"] >= self.settings.get()["thresholds"][g["key"]]["crit"]
                    for g, it in zip(GAUGES, items))
        from PIL import ImageTk

        img = self.renderer.render(items, status, now)
        if self.photo is None or self.photo.width() != img.width or self.photo.height() != img.height:
            self.photo = ImageTk.PhotoImage(img)
            self.label.configure(image=self.photo)
        else:
            self.photo.paste(img)
        # 30 khung/giây khi kim đang chạy hoặc đang báo động; còn lại 2 lần/giây (gần như không tốn CPU)
        self.root.after(33 if (moving or alarm) else 500, self._tick)
        if now - self.last_topmost > 5:
            self.last_topmost = now
            self.win.attributes("-topmost", True)  # giữ trên cùng kể cả khi app khác giành topmost


def set_dpi_awareness() -> None:
    """Gọi trước khi tạo Tk: tránh Windows phóng to mờ ở màn 150%."""
    if not IS_WINDOWS:
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def create_root():
    """Trả về Tk root ẩn, hoặc None nếu máy không có giao diện đồ hoạ / thiếu tkinter."""
    if not IS_WINDOWS and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return None
    try:
        import tkinter as tk
        from PIL import ImageTk  # noqa: F401 - kiểm tra Pillow có hỗ trợ Tk

        set_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        root.title("Thermal Core HUD")
        return root
    except Exception:  # noqa: BLE001 - thiếu Tk/Pillow hay không có màn hình -> tắt overlay
        return None
