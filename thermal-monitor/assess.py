"""Nhận định tình trạng nhiệt của máy bằng lời dễ hiểu cho người không chuyên.

Không chỉ so nhiệt độ với ngưỡng: còn xét mức tải CPU (nóng khi đang chạy nặng là bình thường,
nóng khi đang nghỉ mới là dấu hiệu tản nhiệt kém), tốc độ tăng nhiệt và khoảng cách tới TjMax.
Chỉ kết luận "tản nhiệt tốt/kém" khi có số liệu tải để làm căn cứ.
"""

from __future__ import annotations

LEVELS = ["cool", "normal", "warm", "hot", "critical"]  # thứ tự từ tốt tới xấu
SHORT = {"cool": "RẤT MÁT", "normal": "BÌNH THƯỜNG", "warm": "HƠI ẤM", "hot": "NÓNG",
         "critical": "QUÁ NHIỆT", "unknown": "CHƯA RÕ"}
ICON = {"cool": "❄", "normal": "✓", "warm": "▲", "hot": "⚠", "critical": "⛔", "unknown": "?"}
LABEL = {"cpu": "CPU", "gpu": "GPU", "board": "Mainboard"}
RATE_WINDOW = 60  # giây – tính tốc độ tăng nhiệt trên khoảng này


def level_of(t: float, warn: float, crit: float) -> str:
    if t >= crit:
        return "critical"
    if t >= warn:
        return "hot"
    if warn - t < 10:
        return "warm"
    if warn - t < 25:
        return "normal"
    return "cool"


def score_of(t: float, warn: float, crit: float) -> float:
    """100 = rất mát; 60 = chạm ngưỡng cảnh báo; 20 = chạm ngưỡng nguy hiểm."""
    if t <= warn - 25:
        return 100.0
    if t <= warn:
        return 100 - 40 * (t - (warn - 25)) / 25
    if t <= crit:
        return 60 - 40 * (t - warn) / max(1.0, crit - warn)
    return 10.0


def rate_per_min(history: list[tuple[float, float]] | None) -> float | None:
    """°C/phút từ lịch sử [(thời điểm, giá trị)] (mới nhất ở cuối)."""
    if not history or len(history) < 2:
        return None
    t1, v1 = history[-1]
    older = [(t, v) for t, v in history if t1 - t >= RATE_WINDOW * 0.75]
    if not older:
        return None
    t0, v0 = older[-1]
    return (v1 - v0) * 60 / (t1 - t0)


def throttle_state(metrics: dict, cpu: dict | None, peak_clock: float | None) -> dict | None:
    """Suy luận CPU hạ xung (LHM không báo trực tiếp): chỉ xét khi CPU đang tải nặng (≥ 50%).

    - "thermal": xung < 80% mức cao nhất từng đạt khi tải nặng VÀ CPU đang rất nóng (gần TjMax/ngưỡng nguy hiểm)
    - "power":   tải ≥ 80%, xung thấp nhưng CPU không nóng -> giới hạn điện năng/tiết kiệm pin, không do nhiệt
    """
    # Dùng xung của nhân nhanh nhất: xung trung bình tự thấp khi nhiều nhân rảnh, dễ báo nhầm
    clock, load = metrics.get("cpu_clock_max") or metrics.get("cpu_clock"), metrics.get("cpu_load")
    if clock is None or load is None or not peak_clock or peak_clock < 800:
        return None
    ratio = clock / peak_clock
    info = {"clock": clock, "peak": round(peak_clock), "ratio": round(ratio * 100)}
    if load < 50 or ratio >= 0.8:
        return {**info, "state": "none"}
    tjmax = metrics.get("tjmax")
    hot = cpu is not None and ((tjmax and cpu["value"] >= tjmax - 5) or cpu["value"] >= cpu["crit"])
    if hot:
        return {**info, "state": "thermal"}
    # Không nóng mà xung thấp: chỉ kết luận "giới hạn công suất" khi CPU thực sự chạy hết sức (≥ 80%)
    return {**info, "state": "power" if load >= 80 else "none"}


def drive_notes(metrics: dict) -> tuple[list[str], list[str], str | None]:
    """Sức khoẻ ổ SSD: (ghi chú, lời khuyên, mức xấu nhất)."""
    notes, tips, worst = [], [], None
    for d in metrics.get("drives") or []:
        life = d.get("life")
        if life is None and d.get("used_pct") is not None:
            life = max(0.0, 100 - d["used_pct"])
        name = d["hardware"]
        if life is not None and life <= 10:
            notes.append(f"Ổ {name} chỉ còn khoảng {life:.0f}% tuổi thọ.")
            tips.append(f"Sao lưu dữ liệu và chuẩn bị thay ổ {name}.")
            worst = "hot"
        elif life is not None and life <= 30:
            notes.append(f"Ổ {name} còn {life:.0f}% tuổi thọ – nên theo dõi.")
            worst = worst or "warm"
        spare, spare_min = d.get("spare"), d.get("spare_min")
        if spare is not None and spare_min is not None and spare <= spare_min:
            notes.append(f"Ổ {name} đã dùng hết vùng dự phòng (spare {spare:.0f}%).")
            tips.append(f"Ổ {name} có dấu hiệu sắp hỏng – sao lưu dữ liệu ngay.")
            worst = "hot"
    return notes, tips, worst


def _component(key: str, snap: dict, settings: dict) -> dict | None:
    by_id = {s["id"]: s for s in snap.get("sensors", [])}
    pick = settings.get("picks", {}).get(key, "auto")
    s = by_id.get(pick) if pick != "auto" else None
    s = s or by_id.get((snap.get("primary") or {}).get(key))
    if s is None or s.get("value") is None:
        return None
    if s["id"] == "derived/igpu":  # iGPU ước tính = CPU Package -> không tính 2 lần
        return None
    th = settings["thresholds"][key]
    warn, crit = th["warn"], th["crit"]
    label = LABEL[key]
    if s["category"] == "storage":
        label = "Ổ SSD"
        if s.get("warn"):  # dùng ngưỡng của chính nhà sản xuất ổ
            warn, crit = s["warn"], s.get("crit") or s["warn"] + 5
    t = s["value"]
    return {"key": key, "label": label, "id": s["id"], "value": t, "warn": warn, "crit": crit,
            "level": level_of(t, warn, crit), "score": score_of(t, warn, crit)}


def chosen_values(snap: dict, settings: dict) -> dict:
    """Giá trị đang hiển thị trên 3 đồng hồ (theo lựa chọn cảm biến) – dùng để ghi nhật ký."""
    out = {}
    for key in ("cpu", "gpu", "board"):
        c = _component(key, snap, settings)
        out[key] = c["value"] if c else None
    return out


def evaluate(snap: dict, settings: dict, history: dict[str, list] | None = None,
             peak_clock: float | None = None) -> dict:
    history = history or {}
    if snap.get("mode") not in ("live", "demo"):
        return {"level": "unknown", "short": SHORT["unknown"], "icon": ICON["unknown"], "score": None,
                "headline": "Chưa đọc được cảm biến nhiệt độ",
                "detail": "Xem dòng thông báo bên dưới để biết cách khắc phục.", "tips": []}

    comps = [c for c in (_component(k, snap, settings) for k in ("cpu", "gpu", "board")) if c]
    # Ổ cứng: so với ngưỡng cảnh báo do chính nhà sản xuất ổ công bố
    for s in snap.get("sensors", []):
        if s["category"] == "storage" and s.get("value") is not None and s.get("warn") \
                and not any(c["id"] == s["id"] for c in comps):
            w, c = s["warn"], s.get("crit") or s["warn"] + 5
            comps.append({"key": "storage", "label": f"Ổ {s['hardware']}", "id": s["id"], "value": s["value"],
                          "warn": w, "crit": c, "level": level_of(s["value"], w, c),
                          "score": score_of(s["value"], w, c)})
    if not comps:
        return {"level": "unknown", "short": SHORT["unknown"], "icon": ICON["unknown"], "score": None,
                "headline": "Chưa đủ dữ liệu để đánh giá",
                "detail": "Chưa có cảm biến CPU/GPU/mainboard nào trả về giá trị.", "tips": []}

    worst = max(comps, key=lambda c: (LEVELS.index(c["level"]), -c["score"]))
    level = worst["level"]
    score = round(min(c["score"] for c in comps))
    m = snap.get("metrics") or {}
    load = m.get("cpu_load")
    cpu = next((c for c in comps if c["key"] == "cpu"), None)
    cpu_rate = rate_per_min(history.get(cpu["id"])) if cpu else None
    tjmax = m.get("tjmax")

    tips: list[str] = []
    notes: list[str] = []
    excellent = False

    if cpu and load is not None:
        if load >= 60 and cpu["level"] in ("cool", "normal"):
            excellent = True
            notes.append(f"CPU đang chạy nặng ({load:.0f}%) mà chỉ {cpu['value']:.0f}°C – tản nhiệt làm việc rất tốt.")
        elif load >= 60 and cpu["level"] in ("hot", "critical"):
            notes.append(f"Máy nóng vì CPU đang chạy nặng ({load:.0f}%) – đây là phản ứng bình thường khi tải cao.")
            tips.append("Nếu nóng kéo dài: kê cao đáy máy hoặc dùng đế tản nhiệt, đóng bớt ứng dụng nặng.")
        elif load < 20 and cpu["level"] in ("warm", "hot", "critical"):
            notes.append(f"CPU gần như đang nghỉ ({load:.0f}%) nhưng đã {cpu['value']:.0f}°C – "
                         "tản nhiệt có dấu hiệu kém.")
            tips.append("Kiểm tra khe gió có bị che (chăn, đệm, sát tường), vệ sinh bụi quạt; "
                        "keo tản nhiệt dùng trên 2–3 năm nên thay.")
        elif load < 20 and cpu["level"] == "cool":
            notes.append(f"Máy đang nghỉ (CPU {load:.0f}%) và rất mát.")
    if cpu_rate is not None and cpu_rate >= 4:
        notes.append(f"Nhiệt CPU đang tăng nhanh (+{cpu_rate:.0f}°C/phút).")
    elif cpu_rate is not None and cpu_rate <= -4:
        notes.append(f"CPU đang hạ nhiệt ({cpu_rate:.0f}°C/phút).")
    temp_level = level
    throttle = throttle_state(m, cpu, peak_clock)
    if throttle and throttle["state"] == "thermal":
        notes.append(f"CPU đang tự hạ xung vì nóng: chỉ chạy {throttle['clock'] / 1000:.2f} GHz, "
                     f"bằng {throttle['ratio']}% mức cao nhất ({throttle['peak'] / 1000:.2f} GHz) – máy sẽ chậm đi.")
        tips.append("Giảm tải, kê cao máy cho thoáng; nếu hạ xung thường xuyên, nên vệ sinh quạt và thay keo tản nhiệt.")
        if LEVELS.index(level) < LEVELS.index("hot"):
            level = "hot"
    elif throttle and throttle["state"] == "power":
        notes.append(f"CPU đang bị giới hạn xung ({throttle['ratio']}% mức cao nhất) nhưng không phải do nhiệt – "
                     "thường do chế độ tiết kiệm pin/điện năng hoặc giới hạn công suất của máy.")
    elif cpu and tjmax and tjmax - cpu["value"] <= 3:
        notes.append(f"CPU đã chạm ngưỡng TjMax {tjmax:.0f}°C: máy tự giảm xung (chạy chậm lại) để bảo vệ.")
        tips.append("Giảm tải ngay; nếu thường xuyên chạm TjMax khi làm việc nhẹ, nên mang máy đi vệ sinh.")
    d_notes, d_tips, d_level = drive_notes(m)
    notes += d_notes
    tips += d_tips
    if d_level and LEVELS.index(level) < LEVELS.index(d_level):
        level = d_level

    headline = {
        "cool": "Máy đang rất mát mẻ – tản nhiệt hoạt động xuất sắc" if excellent
        else "Máy đang rất mát mẻ – mọi thứ ổn định",
        "normal": "Nhiệt độ bình thường – tản nhiệt tốt" if excellent
        else "Nhiệt độ bình thường – máy hoạt động ổn định",
        "warm": "Máy hơi ấm – vẫn trong ngưỡng an toàn",
        "hot": "Máy đang nóng – nên giảm tải hoặc cải thiện thông gió",
        "critical": "Quá nhiệt! – hãy đóng bớt ứng dụng nặng ngay",
    }[level]
    if level != temp_level and d_level == level and not (throttle and throttle["state"] == "thermal"):
        # Mức xấu đến từ sức khoẻ ổ đĩa, không phải nhiệt độ
        headline = "Nhiệt độ ổn – nhưng ổ cứng cần chú ý" if temp_level in ("cool", "normal") \
            else "Máy hơi ấm và ổ cứng cần chú ý"
    gap_warn = worst["warn"] - worst["value"]
    where = (f"{worst['label']} {worst['value']:.0f}°C, còn {gap_warn:.0f}°C tới ngưỡng cảnh báo" if gap_warn > 0
             else f"{worst['label']} {worst['value']:.0f}°C, vượt ngưỡng cảnh báo {abs(gap_warn):.0f}°C")
    detail = f"Cao nhất: {where}. " + " ".join(notes)
    if level == "critical" and not tips:
        tips.append("Lưu công việc, đóng ứng dụng nặng và để máy nghỉ vài phút.")
    return {"level": level, "short": SHORT[level], "icon": ICON[level], "score": score,
            "headline": headline, "detail": detail.strip(), "tips": tips,
            "hottest": {"label": worst["label"], "value": worst["value"]}, "throttle": throttle}
