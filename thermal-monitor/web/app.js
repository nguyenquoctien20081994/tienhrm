/* Thermal Core HUD – giao diện đồng hồ cơ / sci-fi, cập nhật realtime qua Server-Sent Events. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ cấu hình
  // Ngưỡng mặc định cho ThinkPad X1 Yoga Gen 5 (i7-10610U, TjMax 100 °C; iGPU Intel UHD chung đế với CPU).
  // Có thể chỉnh trong nút CALIBRATE, giá trị được lưu lại trên máy.
  const GAUGES = [
    { key: "cpu",   code: "01", title: "CPU CORE",  min: 20, max: 110, warn: 85, crit: 95, accent: "#22e5ff" },
    { key: "gpu",   code: "02", title: "GPU CORE",  min: 20, max: 110, warn: 85, crit: 95, accent: "#ff3db8" },
    { key: "board", code: "03", title: "MAINBOARD", min: 20, max: 100, warn: 65, crit: 80, accent: "#3dffa2" },
  ];
  const COLORS = { amber: "#ffb020", red: "#ff3b4e", text: "#d3e6fb", muted: "#6e86a3" };
  const HISTORY_SEC = 3600;  // biểu đồ giữ 60 phút gần nhất
  const LEVEL_COLOR = { cool: "#22e5ff", normal: "#3dffa2", warm: "#ffb020", hot: "#ff7a2e", critical: "#ff3b4e", unknown: "#6e86a3" };
  const CAT_LABEL = { cpu: "CPU", gpu: "GPU", board: "MAINBOARD / EC", storage: "STORAGE", other: "OTHER" };
  const FONT_UI = '"Bahnschrift", "Rajdhani", "Segoe UI", sans-serif';
  const FONT_MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace';

  const store = {
    get(k, d) { try { const v = localStorage.getItem("tch." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("tch." + k, JSON.stringify(v)); } catch { /* bỏ qua */ } },
  };

  const settings = {
    unit: store.get("unit", "C"),
    interval: 6,
    historyDays: 30,
    boardSource: "auto",
    ui: { style: "hud", theme: "auto", brand: "thermal", ...store.get("ui", {}) },
    sound: false,
    thresholds: store.get("thresholds", {}),
    picks: store.get("picks", {}),
  };
  for (const g of GAUGES) {
    g.defaults = { warn: g.warn, crit: g.crit };
    const t = settings.thresholds[g.key];
    if (t) { g.warn = t.warn; g.crit = t.crit; }
  }

  // Cài đặt lưu ở app (settings.json) để overlay dùng chung; localStorage chỉ là dự phòng khi chạy DEMO trong trình duyệt
  const hasBackend = location.protocol !== "file:";
  function saveServer(patch) {
    if (!hasBackend || state.demo) return;
    fetch("api/settings", { method: "POST", headers: { "Content-Type": "application/json", "X-TCH": "1" }, body: JSON.stringify(patch) })
      .catch(() => { /* app đã tắt: giữ bản localStorage */ });
  }
  async function loadServerSettings() {
    if (!hasBackend) return;
    try {
      const r = await fetch("api/settings", { cache: "no-store" });
      if (!r.ok) return;
      const s = await r.json();
      settings.unit = s.unit === "F" ? "F" : "C";
      settings.interval = s.interval || 6;
      settings.historyDays = s.history_days || 30;
      settings.boardSource = s.board_source || "auto";
      updateSourceSwitch();
      settings.ui = { style: "hud", theme: "auto", brand: "thermal", ...(s.ui || {}) };
      store.set("ui", settings.ui);
      applyTheme(); updateStyleButton(); applyBrand();
      settings.picks = s.picks || {};
      settings.thresholds = s.thresholds || {};
      for (const g of GAUGES) {
        const t = settings.thresholds[g.key];
        if (t) { g.warn = t.warn; g.crit = t.crit; }
      }
      store.set("unit", settings.unit); store.set("picks", settings.picks); store.set("thresholds", settings.thresholds);
      $("#btnUnit").textContent = "°" + settings.unit;
      state.sensorKey = "";
      rebuildAll();
    } catch { /* backend cũ hoặc chưa sẵn sàng */ }
  }

  // ------------------------------------------------------------------ tiện ích
  const $ = (s, el = document) => el.querySelector(s);
  const c2f = (c) => c * 9 / 5 + 32;
  const conv = (c) => (settings.unit === "F" ? c2f(c) : c);
  const fmt = (c, d = 1) => (c == null || Number.isNaN(c) ? "--" : conv(c).toFixed(d));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const levelOf = (g, v) => (v == null ? "none" : v >= g.crit ? "crit" : v >= g.warn ? "warn" : "ok");
  const colorOf = (g, v) => ({ crit: COLORS.red, warn: COLORS.amber }[levelOf(g, v)] || g.accent);
  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  function scaleOf(g) {
    if (settings.unit === "C") return { min: g.min, max: g.max, major: 10, minor: 2 };
    return { min: Math.floor(c2f(g.min) / 20) * 20, max: Math.ceil(c2f(g.max) / 20) * 20, major: 20, minor: 5 };
  }

  const START = Math.PI * 0.75; // 135°
  const SWEEP = Math.PI * 1.5;  // 270°
  const angleOf = (sc, displayVal) => START + clamp((displayVal - sc.min) / (sc.max - sc.min), -0.02, 1.02) * SWEEP;

  // ------------------------------------------------------------------ trạng thái
  const state = {
    snap: null, lastDataAt: 0, lastDataWall: 0, everLive: false, sensorKey: "", demo: false,
  };

  const gauges = GAUGES.map((cfg) => {
    const el = $("#tplGauge").content.firstElementChild.cloneNode(true);
    el.dataset.key = cfg.key;
    $(".code", el).textContent = cfg.code;
    $("h2", el).textContent = cfg.title;
    $("#gauges").appendChild(el);
    const g = {
      cfg, el,
      dial: $(".dial", el), spark: $(".spark", el), pick: $(".pick", el),
      target: null, disp: cfg.min - 2, vel: 0, gear: 0,
      hist: [], min: null, max: null, sensor: null, size: 0, layers: null, level: "none",
    };
    g.pick.addEventListener("change", () => {
      settings.picks[cfg.key] = g.pick.value;
      store.set("picks", settings.picks);
      saveServer({ picks: settings.picks });
      resetStats(g);
      if (state.snap) applySnapshot(state.snap, true);
    });
    return g;
  });

  function resetStats(g) { g.hist = []; g.min = g.max = null; }

  // ---- chọn nguồn nhiệt độ mainboard: A (LHM / ổ SSD) hoặc B (HWiNFO)
  const boardCard = gauges.find((g) => g.cfg.key === "board").el;
  const srcSwitch = $(".src-switch", boardCard);
  srcSwitch.hidden = false;
  function setBoardSource(v) {
    settings.boardSource = v;
    // Đổi nguồn thì đồng hồ MAINBOARD quay về AUTO – cảm biến chọn tay (vd. ổ SSD) sẽ che mất nguồn mới
    const g = gauges.find((x) => x.cfg.key === "board");
    delete settings.picks.board;
    store.set("picks", settings.picks);
    g.pick.value = "auto";
    saveServer({ board_source: v, picks: settings.picks });
    resetStats(g);
    updateSourceSwitch(state.snap && state.snap.metrics && state.snap.metrics.board_source);
    if (state.snap) applySnapshot(state.snap, true);
  }
  function updateSourceSwitch(status) {
    for (const b of srcSwitch.querySelectorAll("button")) b.classList.toggle("on", b.dataset.src === settings.boardSource);
    const st = $(".src-state", srcSwitch);
    const manual = settings.picks.board && settings.picks.board !== "auto";
    if (manual) {
      // Chọn tay một cảm biến thì nguồn A/B không còn tác dụng với đồng hồ này
      srcSwitch.dataset.state = "fail";
      st.textContent = "⚠ đang chọn tay → chọn AUTO";
      st.title = "Ô chọn cảm biến phía trên đang khoá vào 1 cảm biến cụ thể, nên nút A/B không đổi được số hiển thị. Chọn AUTO để dùng nguồn A/B.";
      return;
    }
    if (settings.boardSource !== "hwinfo" || !status) { st.textContent = ""; st.title = ""; srcSwitch.dataset.state = ""; return; }
    srcSwitch.dataset.state = status.active ? "ok" : "fail";
    st.textContent = status.active ? "✓ đang dùng" : "⚠ lỗi → tạm dùng A";
    st.title = status.error || "";
  }
  srcSwitch.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-src]");
    if (b && b.dataset.src !== settings.boardSource) setBoardSource(b.dataset.src);
  });

  // ------------------------------------------------------------------ vẽ tĩnh (vành, mặt số, vạch chia)
  function buildLayers(g) {
    if (isVt()) return buildGloss(g);
    if (settings.ui.style === "cockpit") return buildTape(g);
    const rect = g.dial.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const S = Math.round(rect.width * dpr);
    if (!S) return;
    g.size = S; g.dpr = dpr;
    g.dial.width = g.dial.height = S;
    const R = S / 2;
    const mk = () => { const c = document.createElement("canvas"); c.width = c.height = S; const x = c.getContext("2d"); x.translate(R, R); return [c, x]; };
    const [bg, b] = mk();
    const [fg, f] = mk();
    const sc = scaleOf(g.cfg);

    // Quầng sáng ngoài
    b.save();
    b.shadowColor = g.cfg.accent; b.shadowBlur = R * 0.08;
    b.beginPath(); b.arc(0, 0, R * 0.97, 0, Math.PI * 2); b.fillStyle = "#0b1320"; b.fill();
    b.restore();

    // Vành kim loại (bezel)
    let metal;
    if (b.createConicGradient) {
      metal = b.createConicGradient(-Math.PI / 4, 0, 0);
      [["#3b4656", 0], ["#c7d2df", 0.12], ["#55616f", 0.25], ["#9aa7b6", 0.4], ["#2b343f", 0.55],
        ["#d5dee9", 0.7], ["#4a5664", 0.85], ["#3b4656", 1]].forEach(([c, s]) => metal.addColorStop(s, c));
    } else {
      metal = b.createLinearGradient(-R, -R, R, R);
      metal.addColorStop(0, "#c7d2df"); metal.addColorStop(0.5, "#3b4656"); metal.addColorStop(1, "#9aa7b6");
    }
    b.beginPath(); b.arc(0, 0, R * 0.97, 0, Math.PI * 2); b.fillStyle = metal; b.fill();

    // Răng cưa khía của vành (knurling)
    b.strokeStyle = "rgba(0,0,0,0.35)"; b.lineWidth = Math.max(1, R * 0.006);
    for (let i = 0; i < 120; i++) {
      const a = (i / 120) * Math.PI * 2;
      b.beginPath(); b.moveTo(Math.cos(a) * R * 0.925, Math.sin(a) * R * 0.925);
      b.lineTo(Math.cos(a) * R * 0.965, Math.sin(a) * R * 0.965); b.stroke();
    }
    // Vòng vát trong
    b.beginPath(); b.arc(0, 0, R * 0.885, 0, Math.PI * 2);
    b.lineWidth = R * 0.03; b.strokeStyle = "#11161d"; b.stroke();
    b.beginPath(); b.arc(0, 0, R * 0.9, 0, Math.PI * 2);
    b.lineWidth = Math.max(1, R * 0.006); b.strokeStyle = "rgba(255,255,255,0.35)"; b.stroke();

    // Ốc vít
    for (const a of [Math.PI * 0.25, Math.PI * 0.75, Math.PI * 1.25, Math.PI * 1.75]) {
      const x = Math.cos(a) * R * 0.945, y = Math.sin(a) * R * 0.945, r = R * 0.024;
      const sg = b.createRadialGradient(x - r * 0.3, y - r * 0.3, r * 0.1, x, y, r);
      sg.addColorStop(0, "#f2f5f8"); sg.addColorStop(1, "#4a5260");
      b.beginPath(); b.arc(x, y, r, 0, Math.PI * 2); b.fillStyle = sg; b.fill();
      b.save(); b.translate(x, y); b.rotate(a + 0.6);
      b.strokeStyle = "#1a1f27"; b.lineWidth = r * 0.3;
      b.beginPath(); b.moveTo(-r * 0.7, 0); b.lineTo(r * 0.7, 0); b.stroke(); b.restore();
    }

    // Mặt số
    const face = b.createRadialGradient(0, -R * 0.2, R * 0.05, 0, 0, R * 0.87);
    face.addColorStop(0, "#112238"); face.addColorStop(0.7, "#060d18"); face.addColorStop(1, "#02050a");
    b.beginPath(); b.arc(0, 0, R * 0.87, 0, Math.PI * 2); b.fillStyle = face; b.fill();
    // Vân guilloche đồng tâm
    b.strokeStyle = "rgba(140,180,220,0.045)"; b.lineWidth = 1;
    for (let r = R * 0.1; r < R * 0.86; r += R * 0.022) { b.beginPath(); b.arc(0, 0, r, 0, Math.PI * 2); b.stroke(); }
    // Lưới radar
    b.strokeStyle = "rgba(34,229,255,0.05)";
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      b.beginPath(); b.moveTo(0, 0); b.lineTo(Math.cos(a) * R * 0.86, Math.sin(a) * R * 0.86); b.stroke();
    }

    // ---- lớp trước: dải vùng nhiệt, vạch chia, số
    const arcR = R * 0.795, arcW = R * 0.032;
    const zones = [[sc.min, conv(g.cfg.warn), g.cfg.accent], [conv(g.cfg.warn), conv(g.cfg.crit), COLORS.amber], [conv(g.cfg.crit), sc.max, COLORS.red]];
    f.lineWidth = arcW; f.globalAlpha = 0.22;
    for (const [a, z, col] of zones) {
      if (z <= a) continue;
      f.beginPath(); f.arc(0, 0, arcR, angleOf(sc, a), angleOf(sc, z)); f.strokeStyle = col; f.stroke();
    }
    f.globalAlpha = 1;

    for (let v = sc.min; v <= sc.max + 1e-6; v += sc.minor) {
      const major = Math.abs((v - sc.min) % sc.major) < 1e-6;
      const a = angleOf(sc, v);
      const r1 = R * 0.755, r2 = major ? R * 0.68 : R * 0.725;
      f.beginPath();
      f.moveTo(Math.cos(a) * r1, Math.sin(a) * r1); f.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
      const vc = settings.unit === "F" ? (v - 32) * 5 / 9 : v;
      f.strokeStyle = major ? (vc >= g.cfg.crit ? COLORS.red : vc >= g.cfg.warn ? COLORS.amber : "#e3eefa") : "#5f7590";
      f.lineWidth = major ? R * 0.013 : Math.max(1, R * 0.0055);
      f.stroke();
      // Đồng hồ nhỏ (cửa sổ hẹp): chữ to hơn tương đối và chỉ ghi số cách 20 độ để khỏi chồng chéo
      const compact = R < 95 * g.dpr;
      if (major && (!compact || Math.abs((v - sc.min) % (sc.major * 2)) < 1e-6)) {
        f.font = `600 ${Math.max(R * 0.072, 9 * g.dpr)}px ${FONT_UI}`;
        f.fillStyle = "#c9d9ea"; f.textAlign = "center"; f.textBaseline = "middle";
        f.fillText(String(v), Math.cos(a) * R * 0.595, Math.sin(a) * R * 0.595);
      }
    }

    // Mặt số phụ (chronograph) – tốc độ thay đổi nhiệt độ
    const sx = 0, sy = -R * 0.3, sr = R * 0.13;
    f.beginPath(); f.arc(sx, sy, sr, 0, Math.PI * 2);
    f.fillStyle = "rgba(0,0,0,0.45)"; f.fill();
    f.lineWidth = Math.max(1, R * 0.006); f.strokeStyle = "rgba(201,168,106,0.7)"; f.stroke();
    for (let i = 0; i <= 10; i++) {
      const a = START + (i / 10) * SWEEP;
      const big = i % 5 === 0;
      f.beginPath();
      f.moveTo(sx + Math.cos(a) * sr * 0.92, sy + Math.sin(a) * sr * 0.92);
      f.lineTo(sx + Math.cos(a) * sr * (big ? 0.68 : 0.8), sy + Math.sin(a) * sr * (big ? 0.68 : 0.8));
      f.strokeStyle = i < 5 ? "#5ad1ff" : i > 5 ? "#ff8a5a" : "#e3eefa"; f.lineWidth = Math.max(1, R * 0.006); f.stroke();
    }
    f.font = `${R * 0.04}px ${FONT_MONO}`; f.fillStyle = COLORS.muted; f.textAlign = "center";
    if (R >= 95 * g.dpr) f.fillText("Δ/min", sx, sy + sr * 0.55);

    // Cửa sổ hiển thị số
    const wx = -R * 0.29, wy = R * 0.3, ww = R * 0.58, wh = R * 0.2;
    f.fillStyle = "rgba(0,0,0,0.65)"; f.strokeStyle = "rgba(201,168,106,0.75)"; f.lineWidth = Math.max(1, R * 0.007);
    roundRect(f, wx, wy, ww, wh, R * 0.025); f.fill(); f.stroke();
    f.font = `${R * 0.04}px ${FONT_MONO}`; f.fillStyle = COLORS.muted; f.textAlign = "center";
    if (R >= 95 * g.dpr) f.fillText(`${g.cfg.key.toUpperCase()} · TEMP °${settings.unit}`, 0, wy + wh + R * 0.055);

    g.layers = { bg, fg, sc };
  }

  function roundRect(x, X, Y, W, H, r) {
    x.beginPath(); x.moveTo(X + r, Y); x.arcTo(X + W, Y, X + W, Y + H, r); x.arcTo(X + W, Y + H, X, Y + H, r);
    x.arcTo(X, Y + H, X, Y, r); x.arcTo(X, Y, X + W, Y, r); x.closePath();
  }

  function gearPath(x, cx, cy, r, teeth, rot) {
    const depth = r * 0.14;
    x.beginPath();
    for (let i = 0; i < teeth; i++) {
      const a0 = rot + (i / teeth) * Math.PI * 2, step = (Math.PI * 2) / teeth;
      const pts = [[a0, r - depth], [a0 + step * 0.15, r], [a0 + step * 0.5, r], [a0 + step * 0.65, r - depth]];
      for (const [a, rr] of pts) x.lineTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
    }
    x.closePath();
    x.moveTo(cx + r * 0.3, cy); x.arc(cx, cy, r * 0.3, 0, Math.PI * 2);
    for (let k = 0; k < 5; k++) { // nan hoa
      const a = rot + (k / 5) * Math.PI * 2;
      x.moveTo(cx + Math.cos(a) * r * 0.3, cy + Math.sin(a) * r * 0.3);
      x.lineTo(cx + Math.cos(a) * (r - depth * 1.6), cy + Math.sin(a) * (r - depth * 1.6));
    }
  }


  // ------------------------------------------------------------------ kiểu VIỆT TÂM: đồng hồ nổi khối 3D, bóng sáng
  const TAU = Math.PI * 2;
  const FONT_VT_NUM = '"Oswald", "Bahnschrift", "Arial Narrow", sans-serif';
  const FONT_VT = '"Nunito", "Segoe UI", sans-serif';
  const GLOSS = {
    day: {
      base0: "#ffffff", base1: "#dfe7f2", shadow: "rgba(22,60,130,0.30)", rimHi: "rgba(255,255,255,1)", rimLo: "rgba(140,162,196,0.55)",
      face0: "#f7faff", face1: "#e9eff7", track: "#e3eaf4", inset: "rgba(18,48,100,0.42)", zoneOk: "rgba(31,111,229,0.16)",
      tick: "#a3b4cb", label: "#5b6f8f", hub0: "#ffffff", hub1: "#e6edf6", hubShadow: "rgba(22,60,130,0.28)",
      ink: "#0c2a66", muted: "#7486a3", bead1: "#d9e3ef", glare: 0.6,
    },
    night: {
      base0: "#20355a", base1: "#0b172d", shadow: "rgba(0,0,0,0.65)", rimHi: "rgba(255,255,255,0.22)", rimLo: "rgba(0,0,0,0.5)",
      face0: "#16294a", face1: "#0e1c35", track: "#0a1528", inset: "rgba(0,0,0,0.85)", zoneOk: "rgba(74,155,255,0.20)",
      tick: "#4a6187", label: "#9fb3d1", hub0: "#27406a", hub1: "#12223f", hubShadow: "rgba(0,0,0,0.6)",
      ink: "#eef4ff", muted: "#90a4c4", bead1: "#c3d1e6", glare: 0.12,
    },
  };
  const GLOSS_LEVEL = {
    ok: ["#79c0ff", "#1f6fe5"], warn: ["#ffd36b", "#f08a00"], crit: ["#ff9a9a", "#d7262d"], none: ["#c3cede", "#93a3ba"],
  };
  const GLOSS_STATE = { ok: "ỔN ĐỊNH", warn: "CAO", crit: "QUÁ NHIỆT" };

  // Cung tròn có 2 đầu bo tròn (dạng viên thuốc uốn cong), tâm ở gốc toạ độ
  function capsArc(x, r, w, a0, a1) {
    const h = w / 2;
    x.beginPath();
    x.arc(0, 0, r + h, a0, a1);
    x.arc(Math.cos(a1) * r, Math.sin(a1) * r, h, a1, a1 + Math.PI);
    x.arc(0, 0, r - h, a1, a0, true);
    x.arc(Math.cos(a0) * r, Math.sin(a0) * r, h, a0 + Math.PI, a0 + TAU);
    x.closePath();
  }

  function buildGloss(g) {
    const rect = g.dial.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const S = Math.round(rect.width * dpr);
    if (!S) return;
    g.size = S; g.dpr = dpr;
    g.dial.width = g.dial.height = S;
    const R = S / 2, sc = scaleOf(g.cfg);
    const P = document.documentElement.dataset.theme === "day" ? GLOSS.day : GLOSS.night;
    const TR = R * 0.68, TW = R * 0.13;
    const c = document.createElement("canvas"); c.width = c.height = S;
    const x = c.getContext("2d"); x.translate(R, R);

    // Đế tròn nổi khỏi mặt thẻ
    x.save();
    x.shadowColor = P.shadow; x.shadowBlur = R * 0.12; x.shadowOffsetY = R * 0.06;
    const base = x.createLinearGradient(0, -R, 0, R);
    base.addColorStop(0, P.base0); base.addColorStop(1, P.base1);
    x.beginPath(); x.arc(0, 0, R * 0.9, 0, TAU); x.fillStyle = base; x.fill();
    x.restore();
    const rim = x.createLinearGradient(0, -R, 0, R);
    rim.addColorStop(0, P.rimHi); rim.addColorStop(0.55, "rgba(255,255,255,0)"); rim.addColorStop(1, P.rimLo);
    x.beginPath(); x.arc(0, 0, R * 0.895, 0, TAU); x.lineWidth = R * 0.014; x.strokeStyle = rim; x.stroke();
    // Mặt lõm bên trong
    const face = x.createLinearGradient(0, -R * 0.84, 0, R * 0.84);
    face.addColorStop(0, P.face1); face.addColorStop(1, P.face0);
    x.beginPath(); x.arc(0, 0, R * 0.84, 0, TAU); x.fillStyle = face; x.fill();

    // Rãnh lõm của vòng đo (bóng đổ bên trong)
    capsArc(x, TR, TW, START, START + SWEEP);
    x.fillStyle = P.track; x.fill();
    x.save();
    capsArc(x, TR, TW, START, START + SWEEP); x.clip();
    x.shadowColor = P.inset; x.shadowBlur = TW * 0.45; x.shadowOffsetY = TW * 0.16;
    capsArc(x, TR, TW, START, START + SWEEP);
    x.lineWidth = TW * 0.5; x.strokeStyle = P.track; x.stroke();
    x.restore();

    // Vạch vùng nhiệt bên ngoài rãnh: xanh (an toàn) – cam (cảnh báo) – đỏ (nguy hiểm)
    const ZR = TR + TW / 2 + R * 0.045, ZW = R * 0.022;
    const zone = (a, z, col) => {
      if (z <= a) return;
      capsArc(x, ZR, ZW, angleOf(sc, a), angleOf(sc, z)); x.fillStyle = col; x.fill();
    };
    zone(sc.min, conv(g.cfg.warn) - 0.4, P.zoneOk);
    zone(conv(g.cfg.warn), conv(g.cfg.crit) - 0.4, "#f2a31b");
    zone(conv(g.cfg.crit), sc.max, "#e0393f");

    // Chấm chia độ + số
    x.textAlign = "center"; x.textBaseline = "middle";
    x.font = `700 ${Math.max(R * 0.072, 8 * dpr)}px ${FONT_VT}`;
    for (let v = sc.min; v <= sc.max + 1e-6; v += sc.major) {
      const a = angleOf(sc, v), r1 = TR - TW / 2 - R * 0.05, r2 = TR - TW / 2 - R * 0.13;
      x.beginPath(); x.arc(Math.cos(a) * r1, Math.sin(a) * r1, R * 0.013, 0, TAU); x.fillStyle = P.tick; x.fill();
      x.fillStyle = P.label; x.fillText(String(v), Math.cos(a) * r2, Math.sin(a) * r2);
    }

    // Tâm vòm nổi
    x.save();
    x.shadowColor = P.hubShadow; x.shadowBlur = R * 0.08; x.shadowOffsetY = R * 0.035;
    const hub = x.createRadialGradient(-R * 0.1, -R * 0.14, R * 0.02, 0, 0, R * 0.36);
    hub.addColorStop(0, P.hub0); hub.addColorStop(1, P.hub1);
    x.beginPath(); x.arc(0, 0, R * 0.34, 0, TAU); x.fillStyle = hub; x.fill();
    x.restore();
    const hubRim = x.createLinearGradient(0, -R * 0.34, 0, R * 0.34);
    hubRim.addColorStop(0, P.rimHi); hubRim.addColorStop(1, "rgba(255,255,255,0)");
    x.beginPath(); x.arc(0, 0, R * 0.335, 0, TAU); x.lineWidth = R * 0.01; x.strokeStyle = hubRim; x.stroke();

    g.layers = { kind: "gloss", bg: c, sc, P, TR, TW, ZR };
  }

  function drawGloss(g, now) {
    const x = g.dial.getContext("2d");
    const S = g.size, R = S / 2, { sc, P, TR, TW, ZR } = g.layers, t = now / 1000;
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.clearRect(0, 0, S, S);
    x.drawImage(g.layers.bg, 0, 0);
    x.translate(R, R);
    const live = g.target != null && !g.stale;
    const v = g.disp, dv = conv(v);
    const lvl = live ? levelOf(g.cfg, v) : "none";
    const [c0, c1] = GLOSS_LEVEL[lvl];
    const a = Math.max(START + 0.02, angleOf(sc, dv));

    // Vòng đo bóng: màu xanh khi ổn, cam khi cao, đỏ khi quá nhiệt
    if (live) {
      // Nhạt ở đầu -> đậm ở đầu kim. Đầu bo tròn lấn ra trước START (vị trí ~0.99 của gradient) nên cuối dải quay về màu nhạt
      const grad = x.createConicGradient(START, 0, 0), e = (a - START) / TAU;
      grad.addColorStop(0, c0); grad.addColorStop(e, c1); grad.addColorStop(Math.max(e + 0.001, 0.95), c1); grad.addColorStop(1, c0);
      x.save();
      x.shadowColor = hexA(c1, 0.45); x.shadowBlur = R * 0.06; x.shadowOffsetY = R * 0.02;
      capsArc(x, TR, TW * 0.8, START, a); x.fillStyle = grad; x.fill();
      x.restore();
      capsArc(x, TR + TW * 0.16, TW * 0.2, START, a); x.fillStyle = "rgba(255,255,255,0.38)"; x.fill();  // vệt sáng
    }
    // Mốc thấp nhất / cao nhất của phiên
    for (const [val, col] of [[g.min, "#1f8fd6"], [g.max, "#e0621b"]]) {
      if (val == null) continue;
      const m = angleOf(sc, conv(val));
      x.beginPath(); x.arc(Math.cos(m) * ZR, Math.sin(m) * ZR, R * 0.024, 0, TAU);
      x.fillStyle = col; x.fill(); x.lineWidth = R * 0.008; x.strokeStyle = "#fff"; x.stroke();
    }
    // Núm 3D ở đầu vòng đo
    if (live) {
      const bx = Math.cos(a) * TR, by = Math.sin(a) * TR, br = TW * 0.66;
      x.save();
      x.shadowColor = "rgba(10,30,70,0.45)"; x.shadowBlur = R * 0.05; x.shadowOffsetY = R * 0.025;
      const bead = x.createRadialGradient(bx - br * 0.35, by - br * 0.45, br * 0.1, bx, by, br);
      bead.addColorStop(0, "#ffffff"); bead.addColorStop(1, P.bead1);
      x.beginPath(); x.arc(bx, by, br, 0, TAU); x.fillStyle = bead; x.fill();
      x.restore();
      const dot = x.createRadialGradient(bx - br * 0.12, by - br * 0.15, 0, bx, by, br * 0.42);
      dot.addColorStop(0, c0); dot.addColorStop(1, c1);
      x.beginPath(); x.arc(bx, by, br * 0.4, 0, TAU); x.fillStyle = dot; x.fill();
    }

    // Số đo ở tâm
    x.textAlign = "center"; x.textBaseline = "middle";
    x.fillStyle = live ? P.ink : P.muted;
    x.font = `600 ${R * 0.27}px ${FONT_VT_NUM}`;
    x.fillText(live ? dv.toFixed(1) : "--", 0, -R * 0.035);
    x.font = `800 ${Math.max(R * 0.068, 8 * g.dpr)}px ${FONT_VT}`;
    x.fillStyle = P.muted;
    if (live) {
      const tr = trendOf(g);
      const trTxt = tr == null ? "" : `  ${tr > 0.3 ? "▲" : tr < -0.3 ? "▼" : "•"} ${(settings.unit === "F" ? tr * 9 / 5 : tr).toFixed(1)}/ph`;
      x.fillText(`°${settings.unit}${trTxt}`, 0, R * 0.19);
    } else {
      x.fillText(g.stale ? "MẤT TÍN HIỆU" : g.sensor ? "ĐANG NGỦ" : "KHÔNG CÓ CẢM BIẾN", 0, R * 0.19);
    }

    // Nhãn trạng thái ở khe dưới vòng đo
    if (live) {
      const label = GLOSS_STATE[lvl], w = R * 0.6, h = R * 0.15, y = R * 0.66;
      x.save();
      x.shadowColor = hexA(c1, 0.4); x.shadowBlur = R * 0.04; x.shadowOffsetY = R * 0.015;
      const pg = x.createLinearGradient(0, y - h / 2, 0, y + h / 2);
      pg.addColorStop(0, c0); pg.addColorStop(1, c1);
      roundRect(x, -w / 2, y - h / 2, w, h, h / 2); x.fillStyle = pg; x.fill();
      x.restore();
      x.font = `800 ${Math.max(R * 0.07, 8 * g.dpr)}px ${FONT_VT}`; x.fillStyle = "#fff";
      x.fillText(label, 0, y + R * 0.004);
    }

    // Mặt kính phản quang
    x.save();
    x.beginPath(); x.arc(0, 0, R * 0.84, 0, TAU); x.clip();
    const glare = x.createLinearGradient(0, -R * 0.84, 0, R * 0.05);
    glare.addColorStop(0, `rgba(255,255,255,${P.glare})`); glare.addColorStop(1, "rgba(255,255,255,0)");
    x.beginPath(); x.ellipse(0, -R * 0.42, R * 0.74, R * 0.42, 0, 0, TAU); x.fillStyle = glare; x.fill();
    x.restore();

    // Quá nhiệt: vành đỏ nhấp nháy
    if (lvl === "crit") {
      x.beginPath(); x.arc(0, 0, R * 0.885, 0, TAU);
      x.lineWidth = R * 0.025; x.strokeStyle = `rgba(215,38,45,${0.35 + 0.35 * Math.sin(t * 7)})`; x.stroke();
    }
  }

  // ------------------------------------------------------------------ vẽ động mỗi khung hình
  // ------------------------------------------------------------------ kiểu BUỒNG LÁI: thanh đo dọc
  function buildTape(g) {
    const rect = g.dial.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const S = Math.round(rect.width * dpr);
    if (!S) return;
    g.size = S; g.dpr = dpr;
    g.dial.width = g.dial.height = S;
    const sc = scaleOf(g.cfg);
    const T = { x: S * 0.16, y: S * 0.1, w: S * 0.15, h: S * 0.8 };
    const yOf = (dv) => T.y + T.h - clamp((dv - sc.min) / (sc.max - sc.min), -0.01, 1.01) * T.h;
    const c = document.createElement("canvas"); c.width = c.height = S;
    const x = c.getContext("2d");

    // Tấm kim loại + ốc vít
    const plate = x.createLinearGradient(0, 0, 0, S);
    plate.addColorStop(0, "#101b2b"); plate.addColorStop(1, "#04080f");
    roundRect(x, S * 0.03, S * 0.03, S * 0.94, S * 0.94, S * 0.04); x.fillStyle = plate; x.fill();
    const rim = x.createLinearGradient(0, 0, S, S);
    rim.addColorStop(0, "#c7d2df"); rim.addColorStop(0.5, "#3b4656"); rim.addColorStop(1, "#9aa7b6");
    x.lineWidth = S * 0.012; x.strokeStyle = rim; x.stroke();
    for (const [sx, sy] of [[0.08, 0.08], [0.92, 0.08], [0.08, 0.92], [0.92, 0.92]]) {
      const r = S * 0.018, cx = S * sx, cy = S * sy;
      const sg = x.createRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 0.1, cx, cy, r);
      sg.addColorStop(0, "#f2f5f8"); sg.addColorStop(1, "#4a5260");
      x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fillStyle = sg; x.fill();
      x.beginPath(); x.moveTo(cx - r * 0.6, cy); x.lineTo(cx + r * 0.6, cy); x.strokeStyle = "#1a1f27"; x.lineWidth = r * 0.3; x.stroke();
    }
    // Lưới nền
    x.strokeStyle = "rgba(34,229,255,0.05)"; x.lineWidth = 1;
    for (let i = 1; i < 10; i++) { x.beginPath(); x.moveTo(S * 0.05, S * i / 10); x.lineTo(S * 0.95, S * i / 10); x.stroke(); }
    // Rãnh thanh đo
    roundRect(x, T.x - S * 0.012, T.y - S * 0.012, T.w + S * 0.024, T.h + S * 0.024, S * 0.02);
    x.fillStyle = "#02050a"; x.fill(); x.strokeStyle = "rgba(201,168,106,0.7)"; x.lineWidth = Math.max(1, S * 0.004); x.stroke();
    // Dải vùng nhiệt bên trái
    const zones = [[sc.min, conv(g.cfg.warn), g.cfg.accent], [conv(g.cfg.warn), conv(g.cfg.crit), COLORS.amber], [conv(g.cfg.crit), sc.max, COLORS.red]];
    for (const [a, z, col] of zones) {
      if (z <= a) continue;
      x.fillStyle = col; x.globalAlpha = 0.85;
      x.fillRect(T.x - S * 0.05, yOf(z), S * 0.018, yOf(a) - yOf(z));
    }
    x.globalAlpha = 1;
    // Vạch chia + số
    for (let v = sc.min; v <= sc.max + 1e-6; v += sc.minor) {
      const major = Math.abs((v - sc.min) % sc.major) < 1e-6, y = yOf(v);
      x.beginPath(); x.moveTo(T.x + T.w + S * 0.015, y); x.lineTo(T.x + T.w + S * (major ? 0.06 : 0.035), y);
      x.strokeStyle = major ? "#e3eefa" : "#5f7590"; x.lineWidth = major ? S * 0.006 : Math.max(1, S * 0.003); x.stroke();
      if (major) {
        x.font = `600 ${Math.max(S * 0.04, 9 * dpr)}px ${FONT_UI}`; x.fillStyle = "#c9d9ea";
        x.textAlign = "left"; x.textBaseline = "middle"; x.fillText(String(v), T.x + T.w + S * 0.075, y);
      }
    }
    // Khung số đọc bên phải
    roundRect(x, S * 0.5, S * 0.33, S * 0.42, S * 0.24, S * 0.025);
    x.fillStyle = "rgba(0,0,0,0.6)"; x.fill(); x.strokeStyle = "rgba(201,168,106,0.75)"; x.lineWidth = Math.max(1, S * 0.004); x.stroke();
    x.font = `600 ${Math.max(S * 0.04, 9 * dpr)}px ${FONT_MONO}`; x.fillStyle = COLORS.muted; x.textAlign = "center";
    x.fillText(`${g.cfg.key.toUpperCase()} · °${settings.unit}`, S * 0.71, S * 0.29);
    g.layers = { kind: "tape", bg: c, sc, T, yOf };
  }

  function drawTape(g, now) {
    const x = g.dial.getContext("2d");
    const S = g.size, { sc, T, yOf } = g.layers, t = now / 1000;
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.clearRect(0, 0, S, S);
    x.drawImage(g.layers.bg, 0, 0);
    const live = g.target != null && !g.stale;
    const v = g.disp, dv = conv(v), col = live ? colorOf(g.cfg, v) : COLORS.muted;
    const top = yOf(dv), bottom = T.y + T.h;
    if (live && bottom > top) {
      // Cột chất lỏng phát sáng + vệt sáng chạy lên
      const grad = x.createLinearGradient(0, bottom, 0, top);
      grad.addColorStop(0, hexA(g.cfg.accent, 0.55)); grad.addColorStop(1, col);
      x.save(); x.shadowColor = col; x.shadowBlur = S * 0.04;
      x.fillStyle = grad; x.fillRect(T.x, top, T.w, bottom - top); x.restore();
      const band = top + ((t * 0.25) % 1) * (bottom - top);
      const sh = x.createLinearGradient(0, band - S * 0.05, 0, band + S * 0.05);
      sh.addColorStop(0, "rgba(255,255,255,0)"); sh.addColorStop(0.5, "rgba(255,255,255,0.18)"); sh.addColorStop(1, "rgba(255,255,255,0)");
      x.fillStyle = sh; x.fillRect(T.x, Math.max(top, band - S * 0.05), T.w, Math.min(S * 0.1, bottom - top));
    }
    // Vạch MIN / MAX
    for (const [val, c] of [[g.min, "#5ad1ff"], [g.max, "#ff6b5a"]]) {
      if (val == null) continue;
      const y = yOf(conv(val));
      x.fillStyle = c; x.fillRect(T.x - S * 0.008, y - S * 0.004, T.w + S * 0.016, S * 0.008);
    }
    // Con trỏ hình mũi tên
    x.save(); x.translate(T.x - S * 0.06, top);
    x.beginPath(); x.moveTo(S * 0.04, 0); x.lineTo(0, -S * 0.025); x.lineTo(0, S * 0.025); x.closePath();
    x.fillStyle = live ? "#ff6a3d" : "#55606e"; x.shadowColor = "rgba(0,0,0,0.6)"; x.shadowBlur = S * 0.01; x.fill(); x.restore();
    // Số đọc
    x.textAlign = "center"; x.textBaseline = "middle";
    if (live) {
      x.font = `700 ${S * 0.13}px ${FONT_MONO}`;
      x.fillStyle = "rgba(255,255,255,0.06)"; x.fillText("888.8", S * 0.71, S * 0.45);
      x.fillStyle = col; x.shadowColor = col; x.shadowBlur = S * 0.03; x.fillText(dv.toFixed(1), S * 0.71, S * 0.45); x.shadowBlur = 0;
    } else {
      x.font = `700 ${S * 0.06}px ${FONT_MONO}`;
      x.fillStyle = (Math.floor(t * 2) % 2) ? COLORS.muted : "#34465c";
      x.fillText(g.stale ? "NO SIGNAL" : g.sensor ? "SLEEP" : "NO SENSOR", S * 0.71, S * 0.45);
    }
    // Xu hướng + min/max
    const tr = trendOf(g);
    x.font = `600 ${Math.max(S * 0.042, 9 * g.dpr)}px ${FONT_MONO}`;
    if (tr != null) {
      const up = tr > 0.3, down = tr < -0.3;
      x.fillStyle = up ? "#ff8a5a" : down ? "#5ad1ff" : COLORS.muted;
      x.fillText(`${up ? "▲" : down ? "▼" : "■"} ${(settings.unit === "F" ? tr * 9 / 5 : tr).toFixed(1)}°/min`, S * 0.71, S * 0.64);
    }
    x.fillStyle = COLORS.muted;
    x.fillText(`MIN ${fmt(g.min, 0)}°  MAX ${fmt(g.max, 0)}°`, S * 0.71, S * 0.72);
    if (live && v >= g.cfg.crit) {
      roundRect(x, S * 0.03, S * 0.03, S * 0.94, S * 0.94, S * 0.04);
      x.lineWidth = S * 0.015; x.strokeStyle = `rgba(255,59,78,${0.35 + 0.35 * Math.sin(t * 7)})`; x.stroke();
    }
  }

  function drawDial(g, now) {
    if (!g.layers) return;
    if (g.layers.kind === "tape") return drawTape(g, now);
    if (g.layers.kind === "gloss") return drawGloss(g, now);
    const x = g.dial.getContext("2d");
    const S = g.size, R = S / 2, sc = g.layers.sc;
    const t = now / 1000;
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.clearRect(0, 0, S, S);
    x.drawImage(g.layers.bg, 0, 0);
    x.translate(R, R);

    // Bánh răng: quay nhanh hơn khi nóng hơn
    x.save();
    x.beginPath(); x.arc(0, 0, R * 0.86, 0, Math.PI * 2); x.clip();
    x.lineWidth = Math.max(1, R * 0.006);
    x.strokeStyle = "rgba(201,168,106,0.16)";
    gearPath(x, 0, 0, R * 0.46, 32, g.gear); x.stroke();
    x.strokeStyle = "rgba(201,168,106,0.12)";
    gearPath(x, -R * 0.42, R * 0.4, R * 0.2, 14, -g.gear * (32 / 14)); x.stroke();
    gearPath(x, R * 0.44, R * 0.38, R * 0.17, 12, -g.gear * (32 / 12) + 0.2); x.stroke();
    x.restore();

    x.drawImage(g.layers.fg, -R, -R);

    const live = g.target != null && !g.stale;
    const v = g.disp;
    const dv = conv(v);
    const col = live ? colorOf(g.cfg, v) : COLORS.muted;

    // Dải sáng theo giá trị
    if (live) {
      const arcR = R * 0.795;
      const segs = [[g.cfg.min, Math.min(v, g.cfg.warn), g.cfg.accent], [g.cfg.warn, Math.min(v, g.cfg.crit), COLORS.amber], [g.cfg.crit, v, COLORS.red]];
      x.save(); x.lineWidth = R * 0.032; x.lineCap = "butt"; x.shadowBlur = R * 0.06;
      for (const [a, z, c] of segs) {
        if (z <= a) continue;
        x.shadowColor = c; x.strokeStyle = c;
        x.beginPath(); x.arc(0, 0, arcR, angleOf(sc, conv(a)), angleOf(sc, conv(z))); x.stroke();
      }
      x.restore();
    }

    // Vạch MIN / MAX của phiên
    for (const [val, c] of [[g.min, "#5ad1ff"], [g.max, "#ff6b5a"]]) {
      if (val == null) continue;
      const a = angleOf(sc, conv(val));
      x.save(); x.rotate(a);
      x.beginPath(); x.moveTo(R * 0.835, 0); x.lineTo(R * 0.875, -R * 0.02); x.lineTo(R * 0.875, R * 0.02); x.closePath();
      x.fillStyle = c; x.shadowColor = c; x.shadowBlur = R * 0.03; x.fill(); x.restore();
    }

    // Kim mặt số phụ
    const trend = trendOf(g);
    const sa = START + ((clamp(trend ?? 0, -5, 5) + 5) / 10) * SWEEP;
    x.save(); x.translate(0, -R * 0.3); x.rotate(sa);
    x.beginPath(); x.moveTo(-R * 0.02, 0); x.lineTo(R * 0.11, 0);
    x.strokeStyle = "#f2c779"; x.lineWidth = Math.max(1.5, R * 0.008); x.stroke();
    x.beginPath(); x.arc(0, 0, R * 0.012, 0, Math.PI * 2); x.fillStyle = "#f2c779"; x.fill();
    x.restore();

    // Số điện tử
    x.textAlign = "center"; x.textBaseline = "middle";
    const wy = R * 0.4;
    if (live) {
      x.font = `700 ${Math.max(R * 0.135, 10 * g.dpr)}px ${FONT_MONO}`;
      x.fillStyle = "rgba(255,255,255,0.06)"; x.fillText("888.8", 0, wy + R * 0.005);
      x.fillStyle = col; x.shadowColor = col; x.shadowBlur = R * 0.05;
      x.fillText(dv.toFixed(1), 0, wy + R * 0.005);
      x.shadowBlur = 0;
    } else {
      x.font = `700 ${R * 0.075}px ${FONT_MONO}`;
      x.fillStyle = (Math.floor(t * 2) % 2) ? COLORS.muted : "#34465c";
      x.fillText(g.stale ? "NO SIGNAL" : g.sensor ? "SLEEP" : "NO SENSOR", 0, wy);
    }

    // Kim chính
    const a = angleOf(sc, dv);
    x.save(); x.rotate(a);
    x.shadowColor = "rgba(0,0,0,0.7)"; x.shadowBlur = R * 0.03; x.shadowOffsetX = R * 0.012; x.shadowOffsetY = R * 0.018;
    const ng = x.createLinearGradient(0, -R * 0.03, 0, R * 0.03);
    ng.addColorStop(0, live ? "#ff7a45" : "#55606e"); ng.addColorStop(1, live ? "#c4160a" : "#2c333c");
    x.beginPath();
    x.moveTo(R * 0.76, 0); x.lineTo(R * 0.06, -R * 0.02); x.lineTo(-R * 0.1, -R * 0.016);
    x.lineTo(-R * 0.17, 0); x.lineTo(-R * 0.1, R * 0.016); x.lineTo(R * 0.06, R * 0.02); x.closePath();
    x.fillStyle = ng; x.fill();
    x.shadowColor = "transparent";
    x.beginPath(); x.arc(-R * 0.12, 0, R * 0.032, 0, Math.PI * 2); x.fillStyle = live ? "#b3170c" : "#2c333c"; x.fill();
    if (live) { // đường sáng giữa kim
      x.beginPath(); x.moveTo(R * 0.72, 0); x.lineTo(R * 0.08, 0);
      x.strokeStyle = "rgba(255,230,200,0.85)"; x.lineWidth = Math.max(1, R * 0.004);
      x.shadowColor = "#ff7a45"; x.shadowBlur = R * 0.03; x.stroke();
    }
    x.restore();

    // Trục kim bằng đồng + chân kính ruby
    const hub = x.createRadialGradient(-R * 0.02, -R * 0.02, R * 0.005, 0, 0, R * 0.065);
    hub.addColorStop(0, "#fff1c9"); hub.addColorStop(0.5, "#c9a86a"); hub.addColorStop(1, "#5c4724");
    x.beginPath(); x.arc(0, 0, R * 0.065, 0, Math.PI * 2); x.fillStyle = hub; x.fill();
    x.beginPath(); x.arc(0, 0, R * 0.03, 0, Math.PI * 2); x.fillStyle = "#1a1208"; x.fill();
    const ruby = x.createRadialGradient(-R * 0.005, -R * 0.005, 0, 0, 0, R * 0.016);
    ruby.addColorStop(0, "#ffb3c0"); ruby.addColorStop(1, "#a0001d");
    x.beginPath(); x.arc(0, 0, R * 0.016, 0, Math.PI * 2); x.fillStyle = ruby; x.fill();

    // Phản chiếu mặt kính
    x.save();
    x.beginPath(); x.arc(0, 0, R * 0.86, 0, Math.PI * 2); x.clip();
    const glass = x.createLinearGradient(-R, -R, R * 0.3, R * 0.3);
    glass.addColorStop(0, "rgba(255,255,255,0.09)"); glass.addColorStop(0.45, "rgba(255,255,255,0.02)"); glass.addColorStop(0.46, "rgba(255,255,255,0)");
    x.fillStyle = glass; x.fillRect(-R, -R, S, S);
    // Vòng quét radar
    if (live) {
      const sweepA = (t * 0.9) % (Math.PI * 2);
      x.rotate(sweepA);
      const rg = x.createLinearGradient(0, 0, R * 0.86, 0);
      rg.addColorStop(0, "rgba(0,0,0,0)"); rg.addColorStop(1, hexA(g.cfg.accent, 0.12));
      x.beginPath(); x.moveTo(0, 0); x.arc(0, 0, R * 0.86, -0.12, 0); x.closePath(); x.fillStyle = rg; x.fill();
    }
    x.restore();

    // Vòng báo động
    if (live && v >= g.cfg.crit) {
      x.beginPath(); x.arc(0, 0, R * 0.885, 0, Math.PI * 2);
      x.lineWidth = R * 0.02; x.strokeStyle = `rgba(255,59,78,${0.35 + 0.35 * Math.sin(t * 7)})`; x.stroke();
    }
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // °C mỗi phút, so với điểm đo cách ít nhất 45 giây
  function trendOf(g) {
    if (g.hist.length < 2) return null;
    const last = g.hist[g.hist.length - 1];
    const older = g.hist.filter((p) => last.t - p.t >= 45000);
    const ref = older[older.length - 1];
    if (!ref) return null;
    return ((last.v - ref.v) * 60000) / (last.t - ref.t);
  }
  const staleMs = () => (state.snap?.interval || 1) * 2000 + 5000;

  function drawSpark(g) {
    const c = g.spark, dpr = window.devicePixelRatio || 1;
    const W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr);
    if (!W || !H) return;
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const x = c.getContext("2d");
    x.clearRect(0, 0, W, H);
    // lưới
    x.strokeStyle = "rgba(34,229,255,0.07)"; x.lineWidth = 1;
    for (let i = 1; i < 6; i++) { x.beginPath(); x.moveTo((W * i) / 6, 0); x.lineTo((W * i) / 6, H); x.stroke(); }
    const pts = g.hist;
    if (pts.length < 2) return;
    let lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
    if (hi - lo < 8) { const m = (hi + lo) / 2; lo = m - 4; hi = m + 4; }
    lo -= 1; hi += 1;
    const tEnd = pts[pts.length - 1].t;
    const span = Math.min(HISTORY_SEC * 1000, Math.max(5 * 60000, tEnd - pts[0].t));  // 5 → 60 phút
    const X = (t) => W - ((tEnd - t) / span) * W;
    const Y = (v) => H - ((v - lo) / (hi - lo)) * H;

    for (const [lim, col] of [[g.cfg.warn, COLORS.amber], [g.cfg.crit, COLORS.red]]) {
      if (lim > lo && lim < hi) {
        x.setLineDash([4 * dpr, 4 * dpr]); x.strokeStyle = hexA(col, 0.6);
        x.beginPath(); x.moveTo(0, Y(lim)); x.lineTo(W, Y(lim)); x.stroke(); x.setLineDash([]);
      }
    }
    const col = colorOf(g.cfg, pts[pts.length - 1].v);
    x.beginPath();
    pts.forEach((p, i) => (i ? x.lineTo(X(p.t), Y(p.v)) : x.moveTo(X(p.t), Y(p.v))));
    x.strokeStyle = col; x.lineWidth = 1.6 * dpr; x.shadowColor = col; x.shadowBlur = 6 * dpr; x.stroke();
    x.shadowBlur = 0;
    x.lineTo(X(tEnd), H); x.lineTo(X(pts[0].t), H); x.closePath();
    const fill = x.createLinearGradient(0, 0, 0, H);
    fill.addColorStop(0, hexA(col.length === 7 ? col : g.cfg.accent, 0.28)); fill.addColorStop(1, "rgba(0,0,0,0)");
    x.fillStyle = fill; x.fill();
    const lp = pts[pts.length - 1];
    x.beginPath(); x.arc(X(lp.t), Y(lp.v), 3 * dpr, 0, Math.PI * 2); x.fillStyle = "#fff"; x.fill();
    x.font = `${10 * dpr}px ${FONT_MONO}`; x.fillStyle = COLORS.muted; x.textBaseline = "top";
    x.fillText(`${Math.round(span / 60000)} phút`, 4 * dpr, 3 * dpr);
    x.textAlign = "right"; x.fillText(`${fmt(hi, 0)}°`, W - 4 * dpr, 3 * dpr);
    x.textBaseline = "bottom"; x.fillText(`${fmt(lo, 0)}°`, W - 4 * dpr, H - 2 * dpr);
    x.textAlign = "left";
  }

  // ------------------------------------------------------------------ dữ liệu
  function sensorChoiceFor(g, snap) {
    const pick = settings.picks[g.cfg.key] || "auto";
    if (pick !== "auto") {
      const s = snap.sensors.find((x) => x.id === pick);
      if (s) return s;
    }
    const id = snap.primary && snap.primary[g.cfg.key];
    return snap.sensors.find((x) => x.id === id) || null;
  }

  function rebuildPickers(snap) {
    const key = snap.sensors.map((s) => s.id).join("|");
    if (key === state.sensorKey) return;
    state.sensorKey = key;
    for (const g of gauges) {
      const cur = settings.picks[g.cfg.key] || "auto";
      const byCat = {};
      for (const s of snap.sensors) (byCat[s.category] ||= []).push(s);
      let html = `<option value="auto">AUTO</option>`;
      for (const cat of Object.keys(CAT_LABEL)) {
        if (!byCat[cat]) continue;
        html += `<optgroup label="${CAT_LABEL[cat]}">` +
          byCat[cat].map((s) => `<option value="${esc(s.id)}">${esc(s.name)} — ${esc(s.hardware)}</option>`).join("") + `</optgroup>`;
      }
      if (cur !== "auto" && !snap.sensors.some((s) => s.id === cur)) html += `<option value="${esc(cur)}">(không còn) ${esc(cur)}</option>`;
      g.pick.innerHTML = html;
      g.pick.value = cur;
    }
  }

  function applySnapshot(snap, fromPicker = false) {
    state.snap = snap;
    const now = performance.now();
    // Snapshot gửi lại (vd. overlay vừa bật/tắt) có cùng mốc ts -> không tính là lần đo mới
    const fresh = !fromPicker && snap.ts !== state.prevTs;
    if (fresh) { state.prevTs = snap.ts; state.lastDataAt = now; state.lastDataWall = Date.now(); }
    rebuildPickers(snap);

    for (const g of gauges) {
      const s = sensorChoiceFor(g, snap);
      g.sensor = s;
      g.stale = false;
      g.target = s ? s.value : null;
      // Cảm biến thay thế (vd. ổ SSD cho mục MAINBOARD): ghi tên thiết bị cho dễ hiểu
      const autoName = !s ? "—" : s.category !== g.cfg.key && s.category === "storage" ? `Ổ ${s.hardware}` : s.name;
      const autoOpt = g.pick.querySelector('option[value="auto"]');
      if (autoOpt) autoOpt.textContent = `AUTO · ${autoName}`;
      const substitute = s && s.category !== g.cfg.key ? "THAY THẾ · " : "";
      const hw = s ? `${substitute}${s.name} · ${s.hardware}` : "Không có cảm biến – chọn cảm biến khác ở menu phía trên";
      $(".hw", g.el).textContent = hw;
      $(".hw", g.el).title = hw;
      if (g.target != null && fresh) {
        g.hist.push({ t: Date.now(), v: g.target });
        const cut = Date.now() - HISTORY_SEC * 1000;
        while (g.hist.length && g.hist[0].t < cut) g.hist.shift();
        g.min = g.min == null ? g.target : Math.min(g.min, g.target);
        g.max = g.max == null ? g.target : Math.max(g.max, g.target);
      }
      updateCardText(g);
    }
    updateHeader(snap);
    renderVerdict(snap.assessment);
    renderVt(snap.assessment);
    if (isVt()) updateVtMachine(snap);
    if (snap.version) $("#footVersion").textContent = `THERMAL CORE HUD v${snap.version}`;
    renderTele(snap);
    updateSourceSwitch(snap.metrics?.board_source);
    renderBank(snap);
    checkAlarm();
    if (fresh) document.dispatchEvent(new CustomEvent("tch:snapshot", { detail: { snap, demo: state.demo } }));
  }

  function updateCardText(g) {
    const avg = g.hist.length ? g.hist.reduce((a, p) => a + p.v, 0) / g.hist.length : null;
    $(".s-min", g.el).textContent = fmt(g.min);
    $(".s-avg", g.el).textContent = fmt(avg);
    $(".s-max", g.el).textContent = fmt(g.max);
    const tr = trendOf(g);
    const trEl = $(".s-trend", g.el);
    trEl.textContent = tr == null ? "--" : (tr >= 0 ? "+" : "") + (settings.unit === "F" ? tr * 9 / 5 : tr).toFixed(1);
    trEl.style.color = tr == null ? "" : tr > 1.5 ? "#ff8a5a" : tr < -1.5 ? "#5ad1ff" : "";
    const lvl = g.stale ? "none" : levelOf(g.cfg, g.target);
    g.level = lvl;
    g.el.dataset.level = lvl;
    $(".state", g.el).textContent = g.stale ? "NO SIGNAL" : { ok: "NOMINAL", warn: "ELEVATED", crit: "CRITICAL", none: g.sensor ? "SLEEP" : "N/A" }[lvl];
  }

  function updateHeader(snap) {
    const ov = $("#btnOverlay");
    const ovState = state.demo ? null : snap.overlay;  // null/undefined = máy không hỗ trợ overlay
    ov.disabled = ovState == null;
    ov.classList.toggle("on", ovState === true);
    ov.textContent = ovState ? "OVERLAY ON" : "OVERLAY";
    ov.title = ovState == null ? "Overlay cần chạy app trên máy có màn hình (Windows)"
      : "Hiện/ẩn 3 đồng hồ nổi ở góc trên bên phải màn hình, nằm trên mọi cửa sổ";
    const live = snap.mode === "live";
    const pill = $("#linkPill");
    pill.dataset.state = state.demo || snap.mode === "demo" ? "demo" : live ? "live" : "off";
    $("span", pill).textContent = state.demo || snap.mode === "demo" ? "DEMO" : live ? "LIVE" : snap.mode === "nosensor" ? "NO SENSOR" : "ERROR";
    $("#source").textContent = snap.source || "—";
    $("#source").title = snap.source || "";
    const lv = gauges.map((g) => g.level);
    const sys = lv.includes("crit") ? "CRITICAL" : lv.includes("warn") ? "ELEVATED" : lv.includes("ok") ? "NOMINAL" : "STANDBY";
    const sysEl = $("#sysState");
    sysEl.textContent = sys;
    sysEl.style.color = { CRITICAL: COLORS.red, ELEVATED: COLORS.amber, NOMINAL: "#3dffa2" }[sys] || "";

    const msgs = snap.messages || [];
    const bar = $("#msgBar");
    bar.hidden = !msgs.length;
    bar.innerHTML = msgs.map((m) => `<p>⚠ ${esc(m)}</p>`).join("");

    const hot = gauges.filter((g) => g.level === "crit");
    const ab = $("#alertBar");
    ab.hidden = !hot.length;
    ab.textContent = hot.length ? `▲ OVERHEAT WARNING ▲  ${hot.map((g) => `${g.cfg.title} ${fmt(g.target)}°${settings.unit}`).join("  ·  ")}` : "";
  }

  // ---- chế độ ngày/đêm và kiểu đồng hồ
  function applyTheme() {
    const mode = settings.ui.theme;
    const h = new Date().getHours();
    const theme = mode === "auto" ? (h >= 6 && h < 18 ? "day" : "night") : mode;
    if (document.documentElement.dataset.theme !== theme) {
      const first = !document.documentElement.dataset.theme;
      document.documentElement.dataset.theme = theme;
      document.dispatchEvent(new CustomEvent("tch:theme", { detail: theme }));
      if (!first && isVt()) gauges.forEach(buildLayers);  // mặt đồng hồ Việt Tâm đổi màu theo ngày/đêm
    }
    if (isVt()) updateVtTop();
    $("#btnTheme").textContent = { auto: "◐ AUTO", day: "☀ NGÀY", night: "☾ ĐÊM" }[mode];
  }
  function updateStyleButton() {
    $("#btnStyle").textContent = settings.ui.style === "cockpit" ? "COCKPIT" : "HUD";
  }

  // ---- thanh nhận định tình trạng nhiệt (do app tính, xem assess.py)
  function renderVerdict(a) {
    const box = $("#verdict");
    box.hidden = !a;
    if (!a) return;
    box.dataset.level = a.level;
    box.style.setProperty("--lv", LEVEL_COLOR[a.level] || LEVEL_COLOR.unknown);
    $("#vIcon").textContent = a.icon;
    $("#vHeadline").textContent = a.headline;
    $("#vDetail").textContent = a.detail;
    $("#vTips").innerHTML = (a.tips || []).map((t) => `<li>${esc(t)}</li>`).join("");
    $("#vScore").textContent = a.score ?? "--";
    const ring = $("#vRing"), len = 2 * Math.PI * 19;
    ring.style.strokeDasharray = `${len}`;
    ring.style.strokeDashoffset = `${len * (1 - (a.score ?? 0) / 100)}`;
  }

  // ---- giao diện Việt Tâm: 2 nhân vật nói tình trạng máy bằng lời dễ hiểu
  const CREW_LINES = {
    cool: ["Máy đang mát rượi, cứ yên tâm làm việc nhé!", "Tản nhiệt hoạt động rất tốt – máy khoẻ re!"],
    normal: ["Nhiệt độ ổn định, mọi thứ đang bình thường.", "Nhớ để máy ở chỗ thoáng gió để luôn mát như vầy nha!"],
    warm: ["Máy hơi ấm lên rồi, nhưng vẫn trong ngưỡng an toàn.", "Tắt bớt ứng dụng, tab không dùng cho máy nhẹ hơn nhé."],
    hot: ["Máy đang nóng! Mình giảm tải một chút nhé.", "Kiểm tra khe gió, quạt xem có bụi hoặc bị che không nha!"],
    critical: ["Quá nhiệt! Lưu công việc và đóng ứng dụng nặng ngay!", "Nếu bị lặp lại, báo IT/HCNS kiểm tra máy giúp mình nhé."],
    unknown: ["Đang đọc cảm biến…", "Chờ mình một chút nha!"],
  };
  const CREW_LEVELS = Object.keys(CREW_LINES);
  const VT_CHIP = { cool: "RẤT MÁT", normal: "BÌNH THƯỜNG", warm: "HƠI ẤM", hot: "NÓNG", critical: "QUÁ NHIỆT", unknown: "ĐANG ĐỌC" };
  // Màu mức nhiệt trên nền sáng của giao diện Việt Tâm (đậm hơn bản neon để đủ tương phản)
  const VT_LEVEL = { cool: "#1f8fd6", normal: "#1e9a59", warm: "#d98a00", hot: "#e0621b", critical: "#d7262d", unknown: "#7486a3" };
  let brandHave = {};  // ảnh người dùng tự tải lên (thay cho ảnh có sẵn)
  let crewPreloaded = false;
  const isVt = () => settings.ui.brand === "viettam";
  const poseSrc = (slot, level) => (brandHave[slot] ? `api/brand/${slot}?v=${brandStamp}` : `img/vt/${slot}-${level}.webp`);
  function setSrc(img, src) { if (img.getAttribute("src") !== src) img.setAttribute("src", src); }

  // Thẻ chào + thanh lời khuyên: nhân vật đổi tư thế, nhận định nhiệt độ, điểm nhiệt
  function renderVt(a) {
    if (!isVt()) return;
    const level = a?.level || "unknown";
    const lv = VT_LEVEL[level] || VT_LEVEL.unknown;
    const hello = $("#vtHello");
    hello.dataset.level = level;
    hello.style.setProperty("--lv", lv);
    setSrc($("#vtNam"), poseSrc("nam", level));
    setSrc($("#vtNu"), poseSrc("nu", level));
    const [nam, nu] = CREW_LINES[level] || CREW_LINES.unknown;
    $("#vtChip").textContent = VT_CHIP[level] || VT_CHIP.unknown;
    $("#vtTitle").textContent = a ? `Chào bạn! ${a.headline}` : "Chào bạn! Đang đọc cảm biến…";
    $("#vtSay").textContent = nam;
    $("#vtDetail").textContent = a?.detail || "";
    $("#vtTips").innerHTML = (a?.tips || []).slice(1).map((t) => `<li>${esc(t)}</li>`).join("");
    // Có lời khuyên cụ thể (vd. ổ cứng sắp hỏng) thì nhân vật nữ nhắc lời khuyên đó
    $("#vtTipText").textContent = (a?.tips && a.tips[0]) || nu;
    $("#vtScore").textContent = a?.score ?? "--";
    const ring = $("#vtRing"), len = 2 * Math.PI * 50;
    ring.style.strokeDasharray = `${len}`;
    ring.style.strokeDashoffset = `${len * (1 - (a?.score ?? 0) / 100)}`;
    const hot = gauges.filter((g) => g.level === "warn" || g.level === "crit").length;
    const badge = $("#vtBell em");
    badge.hidden = !hot;
    badge.textContent = hot;
  }

  function applyBrand() {
    const vt = isVt();
    const was = document.documentElement.dataset.brand;
    document.documentElement.dataset.brand = vt ? "viettam" : "thermal";
    document.title = vt ? "Việt Tâm · Giám sát nhiệt độ" : "Thermal Core HUD";
    $("#btnBrand").textContent = vt ? "THERMAL" : "VIỆT TÂM";
    $("#btnBrand").title = vt ? "Về giao diện Thermal Core gốc" : "Đổi sang giao diện Việt Tâm";
    $("#favicon").href = vt ? "img/vt/logo.png" : FAVICON_THERMAL;
    if (vt && !crewPreloaded) {  // tải trước mọi tư thế để đổi hình không bị chớp
      crewPreloaded = true;
      for (const slot of ["nam", "nu"]) for (const lv of CREW_LEVELS) new Image().src = `img/vt/${slot}-${lv}.webp`;
    }
    if (vt) {
      loadBrandImages(); renderVt(state.snap?.assessment); updateVtTop();
      Promise.all([document.fonts?.load(`600 20px ${FONT_VT_NUM}`), document.fonts?.load(`800 20px ${FONT_VT}`)])
        .then(() => gauges.forEach(buildLayers)).catch(() => { /* dùng font dự phòng */ });
    }
    if (was && was !== document.documentElement.dataset.brand) {
      // Đồng hồ và biểu đồ vẽ bằng canvas: vẽ lại theo bộ màu mới
      gauges.forEach(buildLayers);
      document.dispatchEvent(new CustomEvent("tch:theme", { detail: document.documentElement.dataset.theme }));
    }
  }
  // Thanh trên của giao diện Việt Tâm: chuông còi cảnh báo, công tắc ngày/đêm
  function updateVtTop() {
    const bell = $("#vtBell");
    bell.classList.toggle("on", settings.sound);
    bell.title = settings.sound ? "Còi cảnh báo quá nhiệt: đang BẬT (bấm để tắt)" : "Còi cảnh báo quá nhiệt: đang tắt (bấm để bật)";
    const sw = $("#vtTheme");
    sw.dataset.mode = document.documentElement.dataset.theme;
    $("span", sw).textContent = { auto: "A", day: "☀", night: "☾" }[settings.ui.theme];
    sw.title = { auto: "Tự động: ngày 6:00–18:00, đêm sau 18:00 (bấm để đổi)", day: "Chế độ ngày (bấm để đổi)", night: "Chế độ đêm (bấm để đổi)" }[settings.ui.theme];
  }
  function updateVtMachine(snap) {
    const cpu = snap.sensors?.find((s) => s.id === snap.primary?.cpu);
    if (cpu) $("#vtCpu").textContent = cpu.hardware;
    const ov = $("#vtOverlay"), ovState = state.demo ? null : snap.overlay;
    ov.disabled = ovState == null;
    ov.classList.toggle("on", ovState === true);
    ov.textContent = ovState ? "Đang hiện đồng hồ nổi" : "Đồng hồ nổi góc màn hình";
    ov.title = $("#btnOverlay").title;
  }
  const FAVICON_THERMAL = $("#favicon").href;
  // Ảnh nhân vật / logo do người dùng tải lên (lưu trong thư mục dữ liệu của app)
  let brandStamp = Date.now();
  async function loadBrandImages() {
    let have = {};
    if (hasBackend && !state.demo) {
      try { const r = await fetch("api/brand", { cache: "no-store" }); if (r.ok) have = await r.json(); } catch { /* app đã tắt */ }
    }
    brandHave = have;
    renderVt(state.snap?.assessment);
    $("#vtLogo").src = have.logo ? `api/brand/logo?v=${brandStamp}` : "img/vt/logo.png";
    for (const row of document.querySelectorAll(".brand-slot")) {
      const slot = row.dataset.slot;
      $(".thumb", row).style.backgroundImage = have[slot] ? `url("api/brand/${slot}?v=${brandStamp}")` : "";
      $(".del", row).disabled = !have[slot];
    }
  }
  // Thu nhỏ ảnh trước khi gửi (tối đa 640 px) để file gọn; xuất PNG để giữ nền trong suốt
  async function shrinkImage(file) {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 640 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return new Promise((ok) => c.toBlob(ok, "image/png"));
  }
  for (const row of document.querySelectorAll(".brand-slot")) {
    const slot = row.dataset.slot, note = $("#brandNote");
    const send = async (method, body) => {
      if (!hasBackend || state.demo) { note.textContent = "Chế độ DEMO trong trình duyệt không lưu được ảnh – hãy chạy app."; return; }
      const r = await fetch(`api/brand/${slot}`, { method, headers: { "X-TCH": "1", "Content-Type": "image/png" }, body });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) { note.textContent = `Không lưu được ảnh: ${res.error || r.status}`; return; }
      note.textContent = method === "DELETE" ? "Đã xoá ảnh – dùng lại nhân vật/logo có sẵn." : "Đã lưu ảnh.";
      brandStamp = Date.now();
      loadBrandImages();
    };
    $("input", row).addEventListener("change", async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      try { await send("POST", await shrinkImage(f)); } catch { note.textContent = "Không đọc được file ảnh này."; }
    });
    $(".del", row).addEventListener("click", () => send("DELETE"));
  }

  // ---- thông số phụ dưới mỗi đồng hồ: tải, công suất, xung, khoảng cách tới TjMax, quạt...
  function renderTele(snap) {
    const m = snap.metrics || {};
    const chip = (label, value, title = "") => `<span title="${esc(title)}"><i>${label}</i>${esc(value)}</span>`;
    for (const g of gauges) {
      const s = g.sensor, out = [];
      if (g.cfg.key === "cpu") {
        if (m.cpu_load != null) out.push(chip("TẢI", `${Math.round(m.cpu_load)}%`, "Mức sử dụng CPU"));
        if (m.cpu_power != null) out.push(chip("CÔNG SUẤT", `${m.cpu_power.toFixed(1)} W`, "Điện năng CPU tiêu thụ"));
        if (m.cpu_clock != null) out.push(chip("XUNG", `${(m.cpu_clock / 1000).toFixed(2)} GHz`, "Xung nhịp trung bình các nhân"));
        const th = snap.assessment?.throttle;
        if (th?.state === "thermal") out.push(`<span class="throttle-thermal" title="CPU đang tự giảm xung vì nóng (suy luận từ xung, tải và nhiệt độ)"><i>⚠ HẠ XUNG DO NHIỆT</i>${th.ratio}%</span>`);
        else if (th?.state === "power") out.push(`<span class="throttle-power" title="Xung thấp hơn mức cao nhất nhưng CPU không nóng: giới hạn điện năng / tiết kiệm pin"><i>GIỚI HẠN XUNG</i>${th.ratio}%</span>`);
        if (m.tjmax != null && s?.value != null) out.push(chip("CÁCH TjMax", `${Math.round(m.tjmax - s.value)}°`,
          `TjMax ${m.tjmax}°C: nhiệt độ mà CPU tự giảm xung để bảo vệ`));
      } else if (g.cfg.key === "gpu") {
        if (s?.id === "derived/igpu") out.push(chip("iGPU", "chung đế CPU", "Intel iGPU không có cảm biến riêng"));
        if (m.gpu_load != null) out.push(chip("TẢI", `${Math.round(m.gpu_load)}%`, "Mức sử dụng GPU"));
      } else {
        if (s?.warn != null) out.push(chip("NGƯỠNG Ổ", `${fmt(s.warn, 0)}°`, "Ngưỡng cảnh báo do nhà sản xuất ổ công bố"));
        for (const f of (m.fans || []).slice(0, 2)) out.push(chip("QUẠT", `${f.rpm} RPM`, `${f.name} · ${f.hardware}`));
      }
      $(".tele", g.el).innerHTML = out.join("");
    }
  }

  // ---- đếm ngược tới lần đo kế tiếp
  function tickCountdown() {
    if (!state.lastDataWall) return;
    const iv = (state.snap?.interval || 1) * 1000;
    const left = Math.max(0, iv - (Date.now() - state.lastDataWall));
    $("#rate").textContent = `${Math.ceil(left / 1000)}s / ${Math.round(iv / 1000)}s`;
    $("#nextBar").style.width = `${100 - (left / iv) * 100}%`;
  }

  let bankKey = "";
  function renderBank(snap) {
    const bank = $("#bank");
    const sensors = snap.sensors || [];
    $("#bankCount").textContent = `${sensors.length} sensors`;
    const fans = snap.metrics?.fans || [];
    const key = sensors.map((s) => s.id).join("|") + "#" + fans.map((f) => f.name + f.hardware).join("|");
    if (key !== bankKey) {
      bankKey = key;
      if (!sensors.length) { bank.innerHTML = `<p class="empty">Chưa có cảm biến nào. Xem thông báo phía trên để biết cách khắc phục.</p>`; return; }
      const byCat = {};
      for (const s of sensors) (byCat[s.category] ||= []).push(s);
      bank.innerHTML = Object.keys(CAT_LABEL).filter((c) => byCat[c]).map((c) => `
        <div class="bank-group"><h3>${CAT_LABEL[c]}</h3><div class="bank-row">
        ${byCat[c].map((s) => `<div class="chip" data-id="${esc(s.id)}"><div class="n"><span>${esc(s.name)}</span><b>--</b></div>
          <small title="${esc(s.hardware)}">${esc(s.hardware)}${s.warn != null ? ` · ngưỡng ${fmt(s.warn, 0)}°` : ""}</small><i class="bar"></i></div>`).join("")}
        </div></div>`).join("") + (fans.length ? `
        <div class="bank-group"><h3>FANS</h3><div class="bank-row">
        ${fans.map((f, i) => `<div class="chip" data-fan="${i}"><div class="n"><span>${esc(f.name)}</span><b>--</b></div>
          <small title="${esc(f.hardware)}">${esc(f.hardware)}</small><i class="bar"></i></div>`).join("")}
        </div></div>` : "");
    }
    const chips = new Map([...bank.querySelectorAll(".chip")].map((el) => [el.dataset.id, el]));
    for (const s of sensors) {
      const el = chips.get(s.id);
      if (!el) continue;
      // Ổ cứng: dùng ngưỡng của chính nhà sản xuất ổ nếu có
      const ref = s.warn != null ? { warn: s.warn, crit: s.crit ?? s.warn + 5, accent: GAUGES[2].accent }
        : GAUGES.find((g) => g.key === s.category) || GAUGES[2];
      const col = s.value == null ? COLORS.muted : colorOf(ref, s.value);
      const b = $("b", el);
      b.textContent = s.value == null ? "--" : `${fmt(s.value)}°`;
      b.style.color = col;
      const bar = $(".bar", el);
      bar.style.width = s.value == null ? "0%" : `${clamp((s.value - 20) / 90, 0, 1) * 100}%`;
      bar.style.background = col;
      bar.style.color = col;
    }
    fans.forEach((f, i) => {
      const el = bank.querySelector(`.chip[data-fan="${i}"]`);
      if (!el) return;
      $("b", el).textContent = `${f.rpm} RPM`;
      $(".bar", el).style.width = `${clamp(f.rpm / 5000, 0, 1) * 100}%`;
    });
  }

  // ------------------------------------------------------------------ còi cảnh báo
  let audio = null, lastBeep = 0;
  function checkAlarm() {
    if (!settings.sound || !gauges.some((g) => g.level === "crit")) return;
    const now = Date.now();
    if (now - lastBeep < 4000) return;
    lastBeep = now;
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.22].forEach((d, i) => {
        const o = audio.createOscillator(), gn = audio.createGain();
        o.type = "square"; o.frequency.value = i ? 660 : 880;
        gn.gain.setValueAtTime(0.0001, audio.currentTime + d);
        gn.gain.exponentialRampToValueAtTime(0.12, audio.currentTime + d + 0.02);
        gn.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + d + 0.2);
        o.connect(gn).connect(audio.destination);
        o.start(audio.currentTime + d); o.stop(audio.currentTime + d + 0.22);
      });
    } catch { /* trình duyệt chặn âm thanh */ }
  }

  // ------------------------------------------------------------------ vòng lặp hoạt ảnh
  let lastFrame = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - lastFrame) / 1000);
    lastFrame = now;
    const stale = state.lastDataAt && now - state.lastDataAt > staleMs();
    for (const g of gauges) {
      if (stale && !g.stale) { g.stale = true; updateCardText(g); }
      const target = g.target != null && !g.stale ? g.target : g.cfg.min - 2;
      // Lò xo giảm chấn: kim có quán tính, hơi vọt nhẹ như đồng hồ cơ
      const k = 70, damp = 12;
      g.vel += ((target - g.disp) * k - g.vel * damp) * dt;
      g.disp += g.vel * dt;
      const frac = clamp((g.disp - g.cfg.min) / (g.cfg.max - g.cfg.min), 0, 1);
      g.gear += (0.08 + frac * frac * 2.2) * dt;
      drawDial(g, now);
    }
    if (stale) {
      const pill = $("#linkPill");
      if (pill.dataset.state !== "off") { pill.dataset.state = "off"; $("span", pill).textContent = "SIGNAL LOST"; }
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ kết nối
  function connect() {
    if (location.protocol === "file:") return startBrowserDemo("Đang mở file HTML trực tiếp nên không có backend đọc cảm biến – hiển thị DEMO. Hãy chạy app bằng start-windows.bat hoặc python app.py.");
    loadServerSettings();
    const es = new EventSource("api/stream");
    es.onmessage = (ev) => {
      try { state.everLive = true; applySnapshot(JSON.parse(ev.data)); } catch (e) { console.error(e); }
    };
    setTimeout(() => {
      if (!state.everLive) { es.close(); startBrowserDemo("Không kết nối được backend (/api/stream) – hiển thị DEMO trong trình duyệt. Số liệu KHÔNG phải nhiệt độ thật."); }
    }, 5000);
  }

  function startBrowserDemo(reason) {
    state.demo = true;
    const st = { cpu: 50, gpu: 45, board: 42 };
    const t0 = Date.now();
    const tick = () => {
      const t = (Date.now() - t0) / 1000;
      const load = 0.5 + 0.5 * Math.sin(t / 17) * Math.sin(t / 5.3);
      const tg = { cpu: 42 + 54 * load, gpu: 40 + 42 * Math.max(0, Math.sin(t / 23)), board: 38 + 14 * load };
      for (const k in st) st[k] += (tg[k] - st[k]) * (k === "cpu" ? 0.35 : 0.12) + (Math.random() - 0.5) * 1.2;
      const r = (v) => Math.round(v * 10) / 10;
      applySnapshot({
        ts: Date.now() / 1000, mode: "demo", source: "Browser simulator", interval: 1, readMs: 0, messages: [reason],
        sensors: [
          { id: "d/cpu", name: "CPU Package", hardware: "Simulated CPU", category: "cpu", value: r(st.cpu) },
          { id: "d/gpu", name: "GPU Core", hardware: "Simulated GPU", category: "gpu", value: r(st.gpu) },
          { id: "d/board", name: "Motherboard", hardware: "Simulated EC", category: "board", value: r(st.board) },
        ],
        primary: { cpu: "d/cpu", gpu: "d/gpu", board: "d/board" },
      });
    };
    tick();
    setInterval(tick, 1000);
  }

  // ------------------------------------------------------------------ điều khiển
  function rebuildAll() { for (const g of gauges) buildLayers(g); if (state.snap) applySnapshot(state.snap, true); }

  $("#btnStyle").onclick = () => {
    settings.ui = { ...settings.ui, style: settings.ui.style === "cockpit" ? "hud" : "cockpit" };
    store.set("ui", settings.ui); saveServer({ ui: settings.ui });
    updateStyleButton(); rebuildAll();
  };
  $("#btnBrand").onclick = () => {
    settings.ui = { ...settings.ui, brand: settings.ui.brand === "viettam" ? "thermal" : "viettam" };
    store.set("ui", settings.ui); saveServer({ ui: settings.ui });
    applyBrand();
  };
  $("#btnTheme").onclick = () => {
    const order = ["auto", "night", "day"];
    settings.ui = { ...settings.ui, theme: order[(order.indexOf(settings.ui.theme) + 1) % order.length] };
    store.set("ui", settings.ui); saveServer({ ui: settings.ui });
    applyTheme();
  };
  setInterval(applyTheme, 60000);  // chế độ tự động đổi theo giờ (6:00–18:00 là ngày)
  applyTheme(); updateStyleButton(); applyBrand();
  console.info("%cThermal Core HUD – thiết kế & phát triển: Nguyễn Quốc Tiến (nguyenquoctien)", "color:#1f6fe5;font-weight:bold");

  const btnUnit = $("#btnUnit");
  btnUnit.textContent = "°" + settings.unit;
  btnUnit.onclick = () => {
    settings.unit = settings.unit === "C" ? "F" : "C";
    store.set("unit", settings.unit);
    saveServer({ unit: settings.unit });
    btnUnit.textContent = "°" + settings.unit;
    rebuildAll();
  };
  const btnSound = $("#btnSound");
  btnSound.onclick = () => {
    settings.sound = !settings.sound;
    btnSound.textContent = settings.sound ? "ALARM ON" : "ALARM OFF";
    btnSound.classList.toggle("on", settings.sound);
    if (isVt()) updateVtTop();
    lastBeep = 0;
    if (settings.sound) { audio ||= new (window.AudioContext || window.webkitAudioContext)(); audio.resume?.(); }
  };
  $("#btnOverlay").onclick = () => {
    fetch("api/overlay", { method: "POST", headers: { "Content-Type": "application/json", "X-TCH": "1" }, body: JSON.stringify({ action: "toggle" }) })
      .catch(() => { /* bỏ qua */ });
  };
  // ---- điều khiển riêng của giao diện Việt Tâm (dùng lại các nút gốc)
  $("#vtBell").onclick = () => btnSound.click();
  $("#vtTheme").onclick = () => $("#btnTheme").click();
  const VT_ACTS = {
    settings: () => $("#btnSettings").click(),
    overlay: () => $("#btnOverlay").click(),
    unit: () => btnUnit.click(),
    classic: () => $("#btnBrand").click(),
  };
  for (const el of document.querySelectorAll("[data-act]")) {
    el.addEventListener("click", (e) => { e.preventDefault(); VT_ACTS[el.dataset.act]?.(); });
  }
  const navLinks = [...document.querySelectorAll('.vt-nav a[href^="#"]:not([data-act])')];
  for (const a of navLinks) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      document.querySelector(a.getAttribute("href"))?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  // Đánh dấu mục đang xem trên menu
  const navSpy = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      for (const a of navLinks) a.classList.toggle("on", a.getAttribute("href") === `#${en.target.id}`);
    }
  }, { rootMargin: "-35% 0px -60% 0px" });
  for (const a of navLinks) { const t = document.querySelector(a.getAttribute("href")); if (t) navSpy.observe(t); }
  if (hasBackend) {
    fetch("api/info").then((r) => r.json()).then((i) => { if (i.host) $("#vtHost").textContent = i.host; }).catch(() => { /* bản cũ */ });
  }

  $("#btnFull").onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.());

  const dlg = $("#settings");
  function fillSettings(useDefaults = false) {
    $("#settingsRows").innerHTML = `<span class="h">ĐỒNG HỒ</span><span class="h">WARN °C</span><span class="h">CRIT °C</span>` +
      GAUGES.map((g) => {
        const v = useDefaults ? g.defaults : g;
        return `<span>${g.title}</span><input type="number" step="1" min="30" max="120" data-k="${g.key}" data-f="warn" value="${v.warn}">` +
          `<input type="number" step="1" min="30" max="125" data-k="${g.key}" data-f="crit" value="${v.crit}">`;
      }).join("");
  }
  $("#btnSettings").onclick = () => {
    fillSettings();
    $("#selInterval").value = String(settings.interval);
    $("#selHistory").value = String(settings.historyDays);
    $("#selBoardSrc").value = settings.boardSource;
    dlg.showModal();
  };
  $("#btnReset").onclick = () => fillSettings(true);
  dlg.addEventListener("close", () => {
    if (dlg.returnValue !== "save") return;
    for (const g of GAUGES) {
      const w = Number($(`input[data-k="${g.key}"][data-f="warn"]`, dlg).value);
      const c = Number($(`input[data-k="${g.key}"][data-f="crit"]`, dlg).value);
      if (Number.isFinite(w) && Number.isFinite(c) && w < c) { g.warn = w; g.crit = c; }
      settings.thresholds[g.key] = { warn: g.warn, crit: g.crit };
    }
    store.set("thresholds", settings.thresholds);
    settings.interval = Number($("#selInterval").value) || 6;
    settings.historyDays = Number($("#selHistory").value) || 30;
    if ($("#selBoardSrc").value !== settings.boardSource) setBoardSource($("#selBoardSrc").value);
    saveServer({ thresholds: settings.thresholds, interval: settings.interval, history_days: settings.historyDays });
    document.dispatchEvent(new CustomEvent("tch:settings"));
    rebuildAll();
  });

  // Đồng hồ thời gian thực ở header
  function clockTick() {
    const d = new Date();
    $("#clock").textContent = d.toLocaleTimeString("vi-VN", { hour12: false });
    $("#date").textContent = d.toLocaleDateString("vi-VN", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }).toUpperCase();
    // Ngày viết tay ở thẻ chào: "Thứ Hai, 19 tháng 10, 2026 · 15:18"
    const wd = ["Chủ nhật", "Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy"][d.getDay()];
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    $("#vtDate").textContent = `${wd}, ${String(d.getDate()).padStart(2, "0")} tháng ${d.getMonth() + 1}, ${d.getFullYear()} · ${hm}`;
  }
  clockTick(); setInterval(clockTick, 1000);
  setInterval(tickCountdown, 500);
  setInterval(() => { for (const g of gauges) drawSpark(g); }, 500);

  let resizeTimer;
  new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => gauges.forEach(buildLayers), 80); }).observe($("#gauges"));
  window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`).addEventListener?.("change", () => gauges.forEach(buildLayers));

  gauges.forEach(buildLayers);
  document.fonts?.ready.then(() => gauges.forEach(buildLayers));  // chữ trên mặt đồng hồ dùng font đóng gói sẵn
  requestAnimationFrame(frame);
  connect();
})();
