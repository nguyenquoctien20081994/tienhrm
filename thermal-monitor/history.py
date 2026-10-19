"""Nhật ký nhiệt độ: lưu mỗi lần đo vào SQLite, giữ 7–30 ngày, tạo báo cáo "giờ nóng nhất trong ngày".

Mỗi lần đo là một dòng (15 giây/lần ≈ 5.760 dòng/ngày ≈ 0,3 MB/ngày), nên 30 ngày chỉ vài MB.
Chỉ dùng thư viện chuẩn (sqlite3).
"""

from __future__ import annotations

import sqlite3
import threading
import time

COLUMNS = ("cpu", "gpu", "board", "cpu_load", "cpu_clock", "throttle", "level")
# Khoảng hiển thị -> (số giây, độ rộng mỗi "ô gộp" tính bằng giây; 0 = điểm gốc)
RANGES = {"1h": (3600, 0), "24h": (86400, 300), "7d": (7 * 86400, 1800), "30d": (30 * 86400, 7200)}


class HistoryDB:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS samples (ts INTEGER NOT NULL, cpu REAL, gpu REAL, board REAL, "
            "cpu_load REAL, cpu_clock REAL, throttle TEXT, level TEXT)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
        self.conn.commit()
        self.last_prune = 0.0

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ------------------------------------------------------------------ ghi
    def record(self, ts: float, values: dict, keep_days: int) -> None:
        row = (int(ts),) + tuple(values.get(c) for c in COLUMNS)
        with self.lock:
            self.conn.execute(f"INSERT INTO samples (ts, {', '.join(COLUMNS)}) VALUES (?{', ?' * len(COLUMNS)})", row)
            if ts - self.last_prune > 3600:  # dọn dữ liệu cũ mỗi giờ
                self.conn.execute("DELETE FROM samples WHERE ts < ?", (int(ts - keep_days * 86400),))
                self.last_prune = ts
            self.conn.commit()

    def peak_clock(self, days: int = 30) -> float | None:
        """Xung cao nhất CPU từng đạt khi tải nặng – mốc để nhận biết hạ xung."""
        with self.lock:
            row = self.conn.execute("SELECT MAX(cpu_clock) FROM samples WHERE cpu_load >= 50 AND ts >= ?",
                                    (int(time.time() - days * 86400),)).fetchone()
        return row[0] if row and row[0] else None

    # ------------------------------------------------------------------ đọc
    def series(self, range_key: str, now: float | None = None) -> dict:
        span, bucket = RANGES.get(range_key, RANGES["24h"])
        now = now or time.time()
        since = int(now - span)
        with self.lock:
            if bucket == 0:
                rows = self.conn.execute(
                    "SELECT ts, cpu, cpu, gpu, gpu, board, board, cpu_load FROM samples WHERE ts >= ? ORDER BY ts",
                    (since,)).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT (ts / ?) * ? + ? / 2 AS t, AVG(cpu), MAX(cpu), AVG(gpu), MAX(gpu), AVG(board), MAX(board), "
                    "AVG(cpu_load) FROM samples WHERE ts >= ? GROUP BY ts / ? ORDER BY t",
                    (bucket, bucket, bucket, since, bucket)).fetchall()
        keys = ("t", "cpu", "cpu_max", "gpu", "gpu_max", "board", "board_max", "load")
        out = {k: [] for k in keys}
        for r in rows:
            for k, v in zip(keys, r):
                out[k].append(v if v is None or k == "t" else round(v, 1))
        out["range"], out["span"], out["bucket"] = range_key, span, bucket
        return out

    def report(self, days: int, thresholds: dict, now: float | None = None) -> dict:
        now = now or time.time()
        since = int(now - days * 86400)
        with self.lock:
            q = self.conn.execute
            n, first, last = q("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples WHERE ts >= ?", (since,)).fetchone()
            if not n:
                return {"days": days, "samples": 0}
            # Khoảng cách điển hình giữa 2 lần đo -> quy đổi số mẫu ra phút
            gaps = [r[0] for r in q("SELECT ts - LAG(ts) OVER (ORDER BY ts) FROM samples WHERE ts >= ? "
                                    "ORDER BY ts DESC LIMIT 200", (since,)).fetchall() if r[0]]
            step = sorted(gaps)[len(gaps) // 2] if gaps else 15
            comps = {}
            for key in ("cpu", "gpu", "board"):
                warn = thresholds[key]["warn"]
                avg, mx, cnt = q(f"SELECT AVG({key}), MAX({key}), COUNT({key}) FROM samples WHERE ts >= ?",
                                 (since,)).fetchone()
                if not cnt:
                    continue
                mx_ts = q(f"SELECT ts FROM samples WHERE ts >= ? AND {key} = ? ORDER BY ts DESC LIMIT 1",
                          (since, mx)).fetchone()[0]
                over = q(f"SELECT COUNT(*) FROM samples WHERE ts >= ? AND {key} >= ?", (since, warn)).fetchone()[0]
                comps[key] = {"avg": round(avg, 1), "max": round(mx, 1), "max_ts": mx_ts,
                              "minutes_over_warn": round(over * step / 60), "warn": warn}
            hours = [None] * 24
            for h, avg, mx, load in q(
                    "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER), AVG(cpu), MAX(cpu), "
                    "AVG(cpu_load) FROM samples WHERE ts >= ? AND cpu IS NOT NULL GROUP BY 1", (since,)).fetchall():
                hours[h] = {"avg": round(avg, 1), "max": round(mx, 1),
                            "load": None if load is None else round(load, 1)}
            heat = [{"day": d, "hour": h, "avg": round(a, 1)} for d, h, a in q(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime'), "
                "CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER), AVG(cpu) "
                "FROM samples WHERE ts >= ? AND cpu IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2",
                (int(now - min(days, 14) * 86400),)).fetchall()]
            throttle_n = q("SELECT COUNT(*) FROM samples WHERE ts >= ? AND throttle = 'thermal'", (since,)).fetchone()[0]
        filled = [(h, v) for h, v in enumerate(hours) if v]
        hottest = max(filled, key=lambda x: x[1]["avg"]) if filled else None
        coolest = min(filled, key=lambda x: x[1]["avg"]) if filled else None
        return {
            "days": days, "samples": n, "first": first, "last": last, "step": step,
            "components": comps, "hours": hours, "heatmap": heat,
            "hottest_hour": hottest and {"hour": hottest[0], **hottest[1]},
            "coolest_hour": coolest and {"hour": coolest[0], **coolest[1]},
            "throttle_minutes": round(throttle_n * step / 60),
            "summary": summarize(days, comps, hottest, coolest, round(throttle_n * step / 60), (last - first) / 3600),
        }


def summarize(days, comps, hottest, coolest, throttle_min, hours_covered) -> list[str]:
    """Vài câu tóm tắt dễ hiểu cho người không chuyên."""
    lines = []
    if hours_covered < 1:
        lines.append("Mới ghi được dưới 1 giờ dữ liệu – báo cáo sẽ chính xác hơn sau vài ngày sử dụng.")
    if hottest:
        h, v = hottest
        lines.append(f"Máy thường nóng nhất khoảng {h:02d}:00–{(h + 1) % 24:02d}:00 "
                     f"(CPU trung bình {v['avg']:.0f}°C" + (f", tải {v['load']:.0f}%)" if v.get("load") is not None else ")"))
    if coolest and hottest and coolest[0] != hottest[0]:
        lines.append(f"Mát nhất khoảng {coolest[0]:02d}:00–{(coolest[0] + 1) % 24:02d}:00 "
                     f"(CPU trung bình {coolest[1]['avg']:.0f}°C).")
    cpu = comps.get("cpu")
    if cpu:
        if cpu["minutes_over_warn"] > 0:
            lines.append(f"CPU vượt ngưỡng cảnh báo ({cpu['warn']}°C) tổng cộng khoảng {cpu['minutes_over_warn']} phút "
                         f"trong {days} ngày qua; cao nhất {cpu['max']:.0f}°C.")
        else:
            lines.append(f"CPU chưa lần nào vượt ngưỡng cảnh báo ({cpu['warn']}°C) trong {days} ngày qua; "
                         f"cao nhất {cpu['max']:.0f}°C.")
    if throttle_min > 0:
        lines.append(f"CPU tự hạ xung vì nóng khoảng {throttle_min} phút – nếu thường xuyên, nên vệ sinh tản nhiệt.")
    return lines


def seed_demo(db: HistoryDB, days: int = 7, step: int = 60, now: float | None = None) -> int:
    """Chế độ DEMO: tạo sẵn vài ngày dữ liệu mô phỏng (nóng hơn buổi chiều, mát về đêm) để xem báo cáo."""
    import math
    import random

    with db.lock:
        if db.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]:
            return 0
    now = int(now or time.time())
    rng = random.Random(42)
    rows = []
    for ts in range(now - days * 86400, now, step):
        hour = time.localtime(ts).tm_hour + time.localtime(ts).tm_min / 60
        work = max(0.0, math.sin((hour - 7) / 12 * math.pi)) if 7 <= hour <= 19 else 0.0
        load = min(100.0, 5 + 70 * work * (0.6 + 0.4 * rng.random()) + (25 if rng.random() < 0.03 else 0))
        cpu = 40 + 0.5 * load + rng.uniform(-2, 2)
        clock = 2700 if (load > 70 and cpu > 85) else 4400  # xung nhân nhanh nhất, khớp DemoProvider
        rows.append((ts, round(cpu, 1), round(cpu - 3, 1), round(36 + 0.12 * load, 1), round(load, 1), clock,
                     "thermal" if clock == 2600 else "none", None))
    with db.lock:
        db.conn.executemany(f"INSERT INTO samples (ts, {', '.join(COLUMNS)}) VALUES (?{', ?' * len(COLUMNS)})", rows)
        db.conn.commit()
    return len(rows)
