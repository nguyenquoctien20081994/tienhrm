/* Thermal Core HUD – bảng phụ: bản đồ nhiệt từng nhân, sức khoẻ ổ cứng, nhật ký nhiệt độ.
   Nhận dữ liệu qua sự kiện "tch:snapshot" do app.js phát; màu lấy từ biến CSS nên tự đổi theo ngày/đêm. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const FONT = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace';
  const T_MIN = 30, T_MAX = 100;  // thang màu nhiệt cố định để các ngày so sánh được với nhau
  const SERIES = [["cpu", "CPU", "--series-cpu"], ["gpu", "GPU", "--series-gpu"], ["board", "BOARD", "--series-board"]];
  const pad2 = (n) => String(n).padStart(2, "0");
  const DOW = ["CN", "T2", "T3", "T4", "T5", "T6", "T7"];

  // ---------------------------------------------------------------- tiện ích vẽ
  function setup(canvas, cssH) {
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth;
    canvas.style.height = cssH + "px";
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(cssH * dpr);
    const x = canvas.getContext("2d");
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.clearRect(0, 0, W, cssH);
    return { x, W, H: cssH };
  }
  function hexRgb(h) {
    const n = parseInt(h.replace("#", ""), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  // Thang 1 sắc (cam): nhạt -> đậm trên nền sáng, tối -> sáng trên nền tối
  function seqColor(temp) {
    const t = clamp((temp - T_MIN) / (T_MAX - T_MIN), 0, 1);
    const a = hexRgb(css("--seq-lo")), b = hexRgb(css("--seq-hi"));
    return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
  }
  function roundTop(x, X, Y, W, H, r) {  // cột có đầu bo tròn 4px, chân phẳng sát trục
    r = Math.min(r, W / 2, H);
    x.beginPath(); x.moveTo(X, Y + H); x.lineTo(X, Y + r); x.arcTo(X, Y, X + r, Y, r);
    x.lineTo(X + W - r, Y); x.arcTo(X + W, Y, X + W, Y + r, r); x.lineTo(X + W, Y + H); x.closePath();
  }
  function tip(canvas, html, px, py) {
    const box = canvas.parentElement.querySelector(".tooltip");
    if (!html) { box.hidden = true; return; }
    box.innerHTML = html; box.hidden = false;
    const bw = box.offsetWidth, cw = canvas.clientWidth;
    box.style.left = `${clamp(px + 14, 0, cw - bw)}px`;
    box.style.top = `${Math.max(0, py - box.offsetHeight - 10)}px`;
  }
  const hhmm = (ts) => { const d = new Date(ts * 1000); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };
  const dayLabel = (d) => `${DOW[d.getDay()]} ${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}`;

  let lastSnap = null, demo = false;

  // ================================================================ 1. CORE MAP
  const coreHist = [];  // [{t, cores:[{name,temp,load,clock}]}]
  let coreNames = "";
  function pushCores(snap) {
    const cores = snap.metrics?.cores || [];
    const names = cores.map((c) => c.name).join("|");
    if (names !== coreNames) { coreHist.length = 0; coreNames = names; }
    if (!cores.length) return;
    // Mỗi cột >= 5 giây (60 phút <= 720 cột) kể cả khi đo 1 giây/lần; cột mới nhất luôn mang số mới
    const last = coreHist[coreHist.length - 1];
    if (last && snap.ts - last.t0 < 5) { last.t = snap.ts; last.cores = cores; } else coreHist.push({ t: snap.ts, t0: snap.ts, cores });
    while (coreHist.length && snap.ts - coreHist[0].t > 3600) coreHist.shift();
  }
  function drawCores() {
    const cv = $("#coreMap");
    const rows = coreHist.length ? coreHist[coreHist.length - 1].cores : [];
    if (!rows.length) {
      const { x, W } = setup(cv, 60);
      x.fillStyle = css("--chart-muted"); x.font = `13px ${FONT}`;
      x.fillText("Máy không cung cấp nhiệt độ từng nhân.", 8, 30);
      cv._hit = null; return;
    }
    const rowH = 22, top = 4, axisH = 18;
    const { x, W, H } = setup(cv, top + rows.length * rowH + axisH);
    const labelW = 96, valueW = 92, gridX = labelW, gridW = Math.max(40, W - labelW - valueW);
    const slots = Math.max(60, Math.min(coreHist.length, 720));
    const cellW = gridW / slots;
    const start = slots - coreHist.length;  // dữ liệu dồn về bên phải (mới nhất)
    x.font = `12px ${FONT}`; x.textBaseline = "middle";
    rows.forEach((c, r) => {
      const y = top + r * rowH;
      x.fillStyle = css("--chart-text"); x.textAlign = "left";
      x.fillText(c.name.replace(/^CPU /, ""), 0, y + rowH / 2);
      coreHist.forEach((h, i) => {
        const v = h.cores[r]?.temp;
        if (v == null) return;
        x.fillStyle = seqColor(v);
        x.fillRect(gridX + (start + i) * cellW, y + 1, Math.max(1, cellW - (cellW > 4 ? 1 : 0)), rowH - 2);
      });
      // Giá trị hiện tại: chữ màu chữ, ô nhỏ mang màu nhiệt
      x.fillStyle = seqColor(c.temp ?? T_MIN); x.fillRect(gridX + gridW + 8, y + 6, 10, 10);
      x.fillStyle = css("--chart-text"); x.textAlign = "left";
      x.fillText(`${c.temp == null ? "--" : c.temp.toFixed(0)}°${c.load != null ? ` ${Math.round(c.load)}%` : ""}`, gridX + gridW + 24, y + rowH / 2);
    });
    if (coreHist.length < slots) {
      x.fillStyle = css("--chart-muted"); x.textAlign = "center"; x.font = `11px ${FONT}`;
      x.fillText("đang ghi…", gridX + (start * cellW) / 2, top + (rows.length * rowH) / 2);
    }
    x.fillStyle = css("--chart-muted"); x.font = `11px ${FONT}`; x.textAlign = "left";
    x.fillText(`${Math.round((slots * (lastSnap?.interval || 15)) / 60)} phút trước`, gridX, H - 8);
    x.textAlign = "right"; x.fillText("bây giờ", gridX + gridW, H - 8);
    cv._hit = { gridX, cellW, start, top, rowH, rows: rows.length };
  }
  $("#coreMap").addEventListener("mousemove", (e) => {
    const cv = e.currentTarget, h = cv._hit;
    if (!h) return tip(cv, null);
    const r = Math.floor((e.offsetY - h.top) / h.rowH), i = Math.floor((e.offsetX - h.gridX) / h.cellW) - h.start;
    const s = coreHist[i], c = s?.cores[r];
    if (r < 0 || r >= h.rows || !c) return tip(cv, null);
    tip(cv, `<b>${esc(c.name)}</b><br>${hhmm(s.t)} · ${c.temp?.toFixed(1) ?? "--"}°C` +
      `${c.load != null ? `<br>Tải ${c.load.toFixed(0)}%` : ""}${c.clock ? ` · ${(c.clock / 1000).toFixed(2)} GHz` : ""}`, e.offsetX, e.offsetY);
  });
  $("#coreMap").addEventListener("mouseleave", (e) => tip(e.currentTarget, null));

  // ================================================================ 2. SỨC KHOẺ Ổ CỨNG
  function driveStatus(d) {
    const life = d.life ?? (d.used_pct != null ? Math.max(0, 100 - d.used_pct) : null);
    if (d.temp != null && d.warn != null && d.temp >= d.warn) return { life, cls: "bad", label: "⚠ QUÁ NÓNG" };
    if (d.spare != null && d.spare_min != null && d.spare <= d.spare_min) return { life, cls: "bad", label: "⛔ SẮP HỎNG" };
    if (life == null) return { life, cls: "", label: "? KHÔNG BÁO TUỔI THỌ" };
    if (life <= 10) return { life, cls: "bad", label: "⛔ NÊN THAY" };
    if (life <= 30) return { life, cls: "watch", label: "▲ THEO DÕI" };
    return { life, cls: "good", label: "✓ TỐT" };
  }
  const tb = (gb) => (gb >= 1024 ? `${(gb / 1024).toFixed(1)} TB` : `${Math.round(gb)} GB`);
  function renderDrives(snap) {
    const drives = snap.metrics?.drives || [];
    const box = $("#drives");
    if (!drives.length) {
      const hasDisk = (snap.sensors || []).some((s) => s.category === "storage");
      box.innerHTML = `<p class="empty-note">${hasDisk ? "Ổ cứng chỉ báo nhiệt độ, không cung cấp chỉ số sức khoẻ S.M.A.R.T."
        : "Chưa có dữ liệu ổ cứng."}</p>`;
      return;
    }
    box.innerHTML = drives.map((d) => {
      const st = driveStatus(d);
      const barCol = { good: css("--green"), watch: css("--amber"), bad: css("--red") }[st.cls] || css("--muted");
      const items = [];
      if (d.temp != null) items.push(["NHIỆT ĐỘ", `${d.temp.toFixed(0)}°C${d.warn != null ? ` / ngưỡng ${d.warn.toFixed(0)}°` : ""}`]);
      if (d.written_gb != null) items.push(["ĐÃ GHI", tb(d.written_gb)]);
      if (d.read_gb != null) items.push(["ĐÃ ĐỌC", tb(d.read_gb)]);
      if (d.hours != null) items.push(["GIỜ HOẠT ĐỘNG", `${Math.round(d.hours).toLocaleString("vi-VN")} giờ (~${(d.hours / 8760).toFixed(1)} năm)`]);
      if (d.power_cycles != null) items.push(["SỐ LẦN BẬT", Math.round(d.power_cycles).toLocaleString("vi-VN")]);
      if (d.space_used != null) items.push(["ĐÃ DÙNG", `${d.space_used.toFixed(0)}%${d.total_gb ? ` của ${tb(d.total_gb)}` : ""}`]);
      if (d.spare != null) items.push(["VÙNG DỰ PHÒNG", `${d.spare.toFixed(0)}%${d.spare_min != null ? ` (tối thiểu ${d.spare_min.toFixed(0)}%)` : ""}`]);
      return `<div class="drive">
        <div class="drive-top"><b title="${esc(d.hardware)}">${esc(d.hardware)}</b><span class="drive-status ${st.cls}">${st.label}</span></div>
        ${st.life != null ? `<div class="life"><div class="life-bar"><i style="width:${clamp(st.life, 0, 100)}%;background:${barCol}"></i></div>
          <strong title="Tuổi thọ còn lại theo ổ tự báo">${st.life.toFixed(0)}%</strong></div>` : ""}
        <dl>${items.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      </div>`;
    }).join("");
  }

  // ================================================================ 3. NHẬT KÝ NHIỆT ĐỘ
  let range = "24h", hist = null, report = null, lastFetch = 0;
  const REPORT_DAYS = { "1h": 1, "24h": 1, "7d": 7, "30d": 30 };
  async function fetchLog(force = false) {
    if (demo) return;
    const now = Date.now();
    const every = range === "1h" ? 0 : 60000;  // khoảng dài: 1 phút cập nhật một lần là đủ
    if (!force && now - lastFetch < every) return;
    lastFetch = now;
    try {
      const [h, r] = await Promise.all([
        fetch(`api/history?range=${range}`, { cache: "no-store" }).then((x) => (x.ok ? x.json() : null)),
        fetch(`api/report?days=${REPORT_DAYS[range]}`, { cache: "no-store" }).then((x) => (x.ok ? x.json() : null)),
      ]);
      hist = h; report = r;
      drawLog();
    } catch { /* app đã tắt */ }
  }
  $("#logTabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-range]");
    if (!b) return;
    range = b.dataset.range;
    for (const x of $("#logTabs").children) x.classList.toggle("on", x === b);
    fetchLog(true);
  });

  function drawLog() {
    const days = REPORT_DAYS[range];
    const sum = $("#logSummary"), tiles = $("#logTiles");
    if (demo || !report) {
      sum.innerHTML = `<li>${demo ? "Nhật ký cần chạy app (ThermalCoreHUD.exe hoặc python app.py)." : "Đang tải nhật ký…"}</li>`;
      tiles.innerHTML = ""; return;
    }
    if (!report.samples) {
      sum.innerHTML = "<li>Chưa có dữ liệu trong khoảng này – app ghi lại mỗi lần đo, hãy quay lại sau ít phút.</li>";
      tiles.innerHTML = "";
    } else {
      sum.innerHTML = report.summary.map((l) => `<li>${esc(l)}</li>`).join("");
      const c = report.components.cpu || {};
      const peakAt = c.max_ts ? `${hhmm(c.max_ts)} ${dayLabel(new Date(c.max_ts * 1000))}` : "";
      tiles.innerHTML = [
        ["CPU TRUNG BÌNH", c.avg != null ? `${c.avg.toFixed(0)}°C` : "--", `${days === 1 ? "24 giờ" : days + " ngày"} qua`],
        ["CPU CAO NHẤT", c.max != null ? `${c.max.toFixed(0)}°C` : "--", peakAt],
        ["VƯỢT NGƯỠNG", `${c.minutes_over_warn ?? 0} phút`, `từ ${c.warn ?? "--"}°C trở lên`],
        ["HẠ XUNG DO NHIỆT", `${report.throttle_minutes ?? 0} phút`, "ước tính"],
      ].map(([k, v, s]) => `<div class="tile"><label>${k}</label><b>${v}</b><small>${esc(s)}</small></div>`).join("");
    }
    drawHistory(); drawHours(); drawDayHeat();
  }

  // ---- 3a. Đường nhiệt độ theo thời gian (CPU / GPU / BOARD)
  function drawHistory() {
    const cv = $("#histChart");
    const { x, W, H } = setup(cv, 240);
    const legend = $("#histLegend");
    const present = SERIES.filter(([k]) => hist?.[k]?.some((v) => v != null));
    const warn = report?.components?.cpu?.warn;
    const band = hist && hist.bucket > 0;
    legend.innerHTML = present.map(([, label, v]) => `<span><i style="background:${css(v)}"></i>${label}</span>`).join("") +
      (warn ? `<span class="ref"><i></i>Ngưỡng cảnh báo CPU ${warn}°</span>` : "") +
      (band && present.some(([k]) => k === "cpu") ? `<span><i style="background:${css("--series-cpu")};opacity:.25;height:8px"></i>Vùng cao nhất CPU</span>` : "");
    if (!hist || !hist.t.length || !present.length) {
      x.fillStyle = css("--chart-muted"); x.font = `13px ${FONT}`; x.fillText("Chưa có dữ liệu.", 10, 30);
      cv._hit = null; return;
    }
    const L = 40, R = 96, T = 10, B = 26, pw = W - L - R, ph = H - T - B;
    const now = Date.now() / 1000, t0 = now - hist.span;
    const vals = present.flatMap(([k]) => hist[k].concat(band ? hist[k + "_max"] : []).filter((v) => v != null));
    let lo = Math.floor((Math.min(...vals, warn ?? 999) - 5) / 10) * 10, hi = Math.ceil((Math.max(...vals, warn ?? 0) + 5) / 10) * 10;
    lo = clamp(lo, 0, 90); hi = clamp(hi, lo + 20, 120);
    const X = (t) => L + ((t - t0) / hist.span) * pw, Y = (v) => T + ph - ((v - lo) / (hi - lo)) * ph;
    // Lưới + trục (nhạt)
    x.font = `11px ${FONT}`; x.textBaseline = "middle"; x.textAlign = "right";
    for (let v = lo; v <= hi; v += 10) {
      x.strokeStyle = css("--chart-grid"); x.lineWidth = 1;
      x.beginPath(); x.moveTo(L, Y(v)); x.lineTo(L + pw, Y(v)); x.stroke();
      x.fillStyle = css("--chart-muted"); x.fillText(`${v}°`, L - 6, Y(v));
    }
    x.textAlign = "center"; x.textBaseline = "top";
    const stepT = { "1h": 600, "24h": 10800, "7d": 86400, "30d": 5 * 86400 }[range];
    for (let t = Math.ceil(t0 / stepT) * stepT; t <= now; t += stepT) {
      const d = new Date(t * 1000);
      x.fillStyle = css("--chart-muted");
      x.fillText(range === "1h" || range === "24h" ? hhmm(t) : dayLabel(d), X(t), T + ph + 8);
    }
    if (warn && warn > lo && warn < hi) {
      x.setLineDash([5, 4]); x.strokeStyle = css("--chart-muted"); x.lineWidth = 1.5;
      x.beginPath(); x.moveTo(L, Y(warn)); x.lineTo(L + pw, Y(warn)); x.stroke(); x.setLineDash([]);
    }
    const gap = Math.max(hist.bucket || (lastSnap?.interval || 15), 15) * 3;  // khoảng trống -> ngắt đường
    const pathOf = (k) => {
      x.beginPath(); let on = false, prev = null;
      hist.t.forEach((t, i) => {
        const v = hist[k][i];
        if (v == null || (prev != null && t - prev > gap)) on = false;
        if (v != null) { on ? x.lineTo(X(t), Y(v)) : x.moveTo(X(t), Y(v)); on = true; }
        prev = t;
      });
    };
    if (band && present.some(([k]) => k === "cpu")) {  // vùng giữa trung bình và cao nhất của CPU
      x.fillStyle = css("--series-cpu"); x.globalAlpha = 0.18;
      hist.t.forEach((t, i) => {
        const a = hist.cpu[i], b = hist.cpu_max[i];
        if (a == null || b == null) return;
        const w = Math.max(1, (hist.bucket / hist.span) * pw);
        x.fillRect(X(t) - w / 2, Y(b), w, Math.max(1, Y(a) - Y(b)));
      });
      x.globalAlpha = 1;
    }
    const ends = [];
    for (const [k, label, v] of present) {
      pathOf(k); x.strokeStyle = css(v); x.lineWidth = 2; x.lineJoin = "round"; x.stroke();
      const i = hist[k].map((val, j) => (val != null ? j : -1)).filter((j) => j >= 0).pop();
      if (i != null) ends.push({ label, color: css(v), y: Y(hist[k][i]), v: hist[k][i], px: X(hist.t[i]) });
    }
    // Nhãn ở cuối đường (tránh chồng nhau)
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    x.textAlign = "left"; x.textBaseline = "middle"; x.font = `12px ${FONT}`;
    for (const e of ends) {
      x.beginPath(); x.arc(e.px, Y(e.v), 4, 0, Math.PI * 2); x.fillStyle = e.color; x.fill();
      x.strokeStyle = css("--panel-2") || "#000"; x.lineWidth = 2; x.stroke();
      x.fillStyle = e.color; x.fillRect(L + pw + 8, e.y - 1.5, 10, 3);
      x.fillStyle = css("--chart-text"); x.fillText(`${e.label} ${e.v.toFixed(0)}°`, L + pw + 22, e.y);
    }
    cv._hit = { L, pw, T, ph, X, Y, present };
    cv._img = cv.width && cv.height ? x.getImageData(0, 0, cv.width, cv.height) : null;  // khung đang ẩn: rộng 0
  }
  $("#histChart").addEventListener("mousemove", (e) => {
    const cv = e.currentTarget, h = cv._hit;
    if (!h || !hist || !cv._img || e.offsetX < h.L || e.offsetX > h.L + h.pw) { tip(cv, null); return; }
    // Điểm gần nhất theo thời gian
    let best = 0, bd = Infinity;
    hist.t.forEach((t, i) => { const d = Math.abs(h.X(t) - e.offsetX); if (d < bd) { bd = d; best = i; } });
    const x = cv.getContext("2d"), dpr = window.devicePixelRatio || 1;
    x.putImageData(cv._img, 0, 0);
    x.save(); x.setTransform(dpr, 0, 0, dpr, 0, 0);
    const px = h.X(hist.t[best]);
    x.strokeStyle = css("--chart-muted"); x.lineWidth = 1; x.beginPath(); x.moveTo(px, h.T); x.lineTo(px, h.T + h.ph); x.stroke();
    const rows = [];
    for (const [k, label, v] of h.present) {
      const val = hist[k][best];
      if (val == null) continue;
      x.beginPath(); x.arc(px, h.Y(val), 4, 0, Math.PI * 2); x.fillStyle = css(v); x.fill();
      x.strokeStyle = css("--panel-2"); x.lineWidth = 2; x.stroke();
      rows.push(`<span class="sw" style="background:${css(v)}"></span>${label} ${val.toFixed(1)}°C${hist[k + "_max"]?.[best] != null && hist.bucket ? ` (cao nhất ${hist[k + "_max"][best].toFixed(0)}°)` : ""}`);
    }
    x.restore();
    const d = new Date(hist.t[best] * 1000);
    const when = range === "1h" || range === "24h" ? `${hhmm(hist.t[best])} ${dayLabel(d)}` : `${dayLabel(d)} ${hhmm(hist.t[best])}`;
    const load = hist.load?.[best];
    tip(cv, `<b>${when}</b><br>${rows.join("<br>")}${load != null ? `<br>Tải CPU ${load.toFixed(0)}%` : ""}`, e.offsetX, e.offsetY);
  });
  $("#histChart").addEventListener("mouseleave", (e) => {
    const cv = e.currentTarget; tip(cv, null);
    if (cv._img) cv.getContext("2d").putImageData(cv._img, 0, 0);
  });

  // ---- 3b. CPU trung bình theo giờ trong ngày (một chuỗi -> không cần chú thích)
  function drawHours() {
    const cv = $("#hourChart");
    const { x, W, H } = setup(cv, 200);
    const hours = report?.hours || [];
    if (!hours.some(Boolean)) { cv._hit = null; x.fillStyle = css("--chart-muted"); x.font = `13px ${FONT}`; x.fillText("Chưa có dữ liệu.", 10, 30); return; }
    const L = 34, R = 6, T = 18, B = 22, pw = W - L - R, ph = H - T - B;
    const lo = 20, hi = Math.max(60, Math.ceil(Math.max(...hours.filter(Boolean).map((h) => h.avg)) / 10) * 10 + 10);
    const Y = (v) => T + ph - ((v - lo) / (hi - lo)) * ph;
    x.font = `11px ${FONT}`; x.textAlign = "right"; x.textBaseline = "middle";
    for (let v = lo; v <= hi; v += 20) {
      x.strokeStyle = css("--chart-grid"); x.beginPath(); x.moveTo(L, Y(v)); x.lineTo(L + pw, Y(v)); x.stroke();
      x.fillStyle = css("--chart-muted"); x.fillText(`${v}°`, L - 5, Y(v));
    }
    const slot = pw / 24, bw = Math.max(2, slot - 2);  // 2px khe giữa các cột
    const hot = report.hottest_hour?.hour;
    x.textAlign = "center"; x.textBaseline = "top";
    hours.forEach((h, i) => {
      if (i % 3 === 0) { x.fillStyle = css("--chart-muted"); x.fillText(pad2(i), L + i * slot + slot / 2, T + ph + 6); }
      if (!h) return;
      roundTop(x, L + i * slot + 1, Y(h.avg), bw, T + ph - Y(h.avg), 4);
      x.fillStyle = css("--series-cpu"); x.fill();
    });
    if (hot != null && hours[hot]) {  // nhãn trực tiếp cho giờ nóng nhất
      x.fillStyle = css("--chart-text"); x.textBaseline = "bottom"; x.font = `600 12px ${FONT}`;
      x.fillText(`${hours[hot].avg.toFixed(0)}°`, L + hot * slot + slot / 2, Y(hours[hot].avg) - 3);
    }
    $("#hourTitle").textContent = `CPU TRUNG BÌNH THEO GIỜ TRONG NGÀY${hot != null ? ` · NÓNG NHẤT ${pad2(hot)}:00` : ""}`;
    cv._hit = { L, slot, hours };
  }
  $("#hourChart").addEventListener("mousemove", (e) => {
    const cv = e.currentTarget, h = cv._hit;
    const i = h ? Math.floor((e.offsetX - h.L) / h.slot) : -1;
    const v = h?.hours[i];
    if (!v) return tip(cv, null);
    tip(cv, `<b>${pad2(i)}:00–${pad2((i + 1) % 24)}:00</b><br>Trung bình ${v.avg.toFixed(1)}°C<br>Cao nhất ${v.max.toFixed(0)}°C` +
      `${v.load != null ? `<br>Tải CPU TB ${v.load.toFixed(0)}%` : ""}`, e.offsetX, e.offsetY);
  });
  $("#hourChart").addEventListener("mouseleave", (e) => tip(e.currentTarget, null));

  // ---- 3c. Bản đồ nhiệt ngày × giờ
  function drawDayHeat() {
    const cv = $("#dayHeat");
    const cells = report?.heatmap || [];
    const days = [...new Set(cells.map((c) => c.day))].slice(-14);
    const rowH = 18, T = 4, B = 20;
    const { x, W, H } = setup(cv, Math.max(80, T + days.length * rowH + B));
    if (!days.length) { cv._hit = null; x.fillStyle = css("--chart-muted"); x.font = `13px ${FONT}`; x.fillText("Chưa có dữ liệu.", 10, 30); return; }
    const L = 70, pw = W - L - 4, cw = pw / 24;
    const map = new Map(cells.map((c) => [`${c.day}|${c.hour}`, c.avg]));
    x.font = `11px ${FONT}`; x.textBaseline = "middle";
    days.forEach((d, r) => {
      const y = T + r * rowH;
      x.fillStyle = css("--chart-text"); x.textAlign = "left";
      x.fillText(dayLabel(new Date(d + "T00:00:00")), 0, y + rowH / 2);
      for (let hh = 0; hh < 24; hh++) {
        const v = map.get(`${d}|${hh}`);
        if (v == null) continue;
        x.fillStyle = seqColor(v);
        x.fillRect(L + hh * cw + 1, y + 1, cw - 2, rowH - 2);  // 2px khe giữa các ô
      }
    });
    x.textAlign = "center"; x.fillStyle = css("--chart-muted");
    for (let hh = 0; hh < 24; hh += 3) x.fillText(pad2(hh), L + hh * cw + cw / 2, H - 9);
    cv._hit = { L, cw, T, rowH, days, map };
  }
  $("#dayHeat").addEventListener("mousemove", (e) => {
    const cv = e.currentTarget, h = cv._hit;
    if (!h) return tip(cv, null);
    const r = Math.floor((e.offsetY - h.T) / h.rowH), hh = Math.floor((e.offsetX - h.L) / h.cw);
    const d = h.days[r], v = d != null && hh >= 0 && hh < 24 ? h.map.get(`${d}|${hh}`) : null;
    if (v == null) return tip(cv, null);
    tip(cv, `<b>${dayLabel(new Date(d + "T00:00:00"))} · ${pad2(hh)}:00</b><br>CPU trung bình ${v.toFixed(1)}°C`, e.offsetX, e.offsetY);
  });
  $("#dayHeat").addEventListener("mouseleave", (e) => tip(e.currentTarget, null));

  // ================================================================ kết nối với app.js
  document.addEventListener("tch:snapshot", (ev) => {
    const { snap } = ev.detail;
    demo = ev.detail.demo;
    lastSnap = snap;
    pushCores(snap); drawCores();
    renderDrives(snap);
    fetchLog();
    if (demo) drawLog();
  });
  const redrawAll = () => { drawCores(); if (lastSnap) renderDrives(lastSnap); drawLog(); };
  document.addEventListener("tch:theme", redrawAll);
  document.addEventListener("tch:layout", redrawAll);  // đổi tab: biểu đồ vừa được hiện ra cần vẽ theo kích thước thật
  document.addEventListener("tch:settings", () => fetchLog(true));
  // Chỉ vẽ lại khi bề ngang đổi (vẽ biểu đồ làm trang cao lên/thấp xuống -> tránh vòng lặp)
  let rt, lastW = 0;
  new ResizeObserver(() => {
    if (document.body.clientWidth === lastW) return;
    lastW = document.body.clientWidth;
    clearTimeout(rt); rt = setTimeout(redrawAll, 120);
  }).observe(document.body);
})();
