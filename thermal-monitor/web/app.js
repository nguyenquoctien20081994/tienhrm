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
  const HISTORY_SEC = 180;
  const STALE_MS = 6000;
  const CAT_LABEL = { cpu: "CPU", gpu: "GPU", board: "MAINBOARD / EC", storage: "STORAGE", other: "OTHER" };
  const FONT_UI = '"Bahnschrift", "Rajdhani", "Segoe UI", sans-serif';
  const FONT_MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace';

  const store = {
    get(k, d) { try { const v = localStorage.getItem("tch." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("tch." + k, JSON.stringify(v)); } catch { /* bỏ qua */ } },
  };

  const settings = {
    unit: store.get("unit", "C"),
    sound: false,
    thresholds: store.get("thresholds", {}),
    picks: store.get("picks", {}),
  };
  for (const g of GAUGES) {
    g.defaults = { warn: g.warn, crit: g.crit };
    const t = settings.thresholds[g.key];
    if (t) { g.warn = t.warn; g.crit = t.crit; }
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
    snap: null, lastDataAt: 0, everLive: false, sensorKey: "", demo: false,
  };

  const gauges = GAUGES.map((cfg) => {
    const el = $("#tplGauge").content.firstElementChild.cloneNode(true);
    el.dataset.key = cfg.key;
    el.style.setProperty("--accent", cfg.accent);
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
      resetStats(g);
      if (state.snap) applySnapshot(state.snap, true);
    });
    return g;
  });

  function resetStats(g) { g.hist = []; g.min = g.max = null; }

  // ------------------------------------------------------------------ vẽ tĩnh (vành, mặt số, vạch chia)
  function buildLayers(g) {
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
      if (major) {
        f.font = `600 ${R * 0.072}px ${FONT_UI}`;
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
    f.fillText("Δ/10s", sx, sy + sr * 0.55);

    // Cửa sổ hiển thị số
    const wx = -R * 0.29, wy = R * 0.3, ww = R * 0.58, wh = R * 0.2;
    f.fillStyle = "rgba(0,0,0,0.65)"; f.strokeStyle = "rgba(201,168,106,0.75)"; f.lineWidth = Math.max(1, R * 0.007);
    roundRect(f, wx, wy, ww, wh, R * 0.025); f.fill(); f.stroke();
    f.font = `${R * 0.04}px ${FONT_MONO}`; f.fillStyle = COLORS.muted; f.textAlign = "center";
    f.fillText(`${g.cfg.key.toUpperCase()} · TEMP °${settings.unit}`, 0, wy + wh + R * 0.055);

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

  // ------------------------------------------------------------------ vẽ động mỗi khung hình
  function drawDial(g, now) {
    if (!g.layers) return;
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
      x.font = `700 ${R * 0.135}px ${FONT_MONO}`;
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

  function trendOf(g) {
    if (g.hist.length < 2) return null;
    const last = g.hist[g.hist.length - 1];
    const ref = g.hist.find((p) => last.t - p.t <= 10000) || g.hist[0];
    if (last.t === ref.t) return null;
    return ((last.v - ref.v) * 10000) / (last.t - ref.t);
  }

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
    const tEnd = pts[pts.length - 1].t, span = HISTORY_SEC * 1000;
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
    x.fillText(`${HISTORY_SEC}s`, 4 * dpr, 3 * dpr);
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
    if (!fromPicker) state.lastDataAt = now;
    rebuildPickers(snap);

    for (const g of gauges) {
      const s = sensorChoiceFor(g, snap);
      g.sensor = s;
      g.stale = false;
      g.target = s ? s.value : null;
      const autoName = s ? `${s.name}` : "—";
      const autoOpt = g.pick.querySelector('option[value="auto"]');
      if (autoOpt) autoOpt.textContent = `AUTO · ${autoName}`;
      const substitute = s && s.category !== g.cfg.key ? "THAY THẾ · " : "";
      const hw = s ? `${substitute}${s.name} · ${s.hardware}` : "Không có cảm biến – chọn cảm biến khác ở menu phía trên";
      $(".hw", g.el).textContent = hw;
      $(".hw", g.el).title = hw;
      if (g.target != null && !fromPicker) {
        g.hist.push({ t: Date.now(), v: g.target });
        const cut = Date.now() - HISTORY_SEC * 1000;
        while (g.hist.length && g.hist[0].t < cut) g.hist.shift();
        g.min = g.min == null ? g.target : Math.min(g.min, g.target);
        g.max = g.max == null ? g.target : Math.max(g.max, g.target);
      }
      updateCardText(g);
    }
    updateHeader(snap);
    renderBank(snap);
    checkAlarm();
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
    const live = snap.mode === "live";
    const pill = $("#linkPill");
    pill.dataset.state = state.demo || snap.mode === "demo" ? "demo" : live ? "live" : "off";
    $("span", pill).textContent = state.demo || snap.mode === "demo" ? "DEMO" : live ? "LIVE" : snap.mode === "nosensor" ? "NO SENSOR" : "ERROR";
    $("#source").textContent = snap.source || "—";
    $("#source").title = snap.source || "";
    $("#rate").textContent = `${(snap.interval || 1).toFixed(2)}s · ${snap.readMs ?? 0}ms`;
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

  let bankKey = "";
  function renderBank(snap) {
    const bank = $("#bank");
    const sensors = snap.sensors || [];
    $("#bankCount").textContent = `${sensors.length} sensors`;
    const key = sensors.map((s) => s.id).join("|");
    if (key !== bankKey) {
      bankKey = key;
      if (!sensors.length) { bank.innerHTML = `<p class="empty">Chưa có cảm biến nào. Xem thông báo phía trên để biết cách khắc phục.</p>`; return; }
      const byCat = {};
      for (const s of sensors) (byCat[s.category] ||= []).push(s);
      bank.innerHTML = Object.keys(CAT_LABEL).filter((c) => byCat[c]).map((c) => `
        <div class="bank-group"><h3>${CAT_LABEL[c]}</h3><div class="bank-row">
        ${byCat[c].map((s) => `<div class="chip" data-id="${esc(s.id)}"><div class="n"><span>${esc(s.name)}</span><b>--</b></div>
          <small title="${esc(s.hardware)}">${esc(s.hardware)}</small><i class="bar"></i></div>`).join("")}
        </div></div>`).join("");
    }
    const chips = new Map([...bank.querySelectorAll(".chip")].map((el) => [el.dataset.id, el]));
    for (const s of sensors) {
      const el = chips.get(s.id);
      if (!el) continue;
      const ref = GAUGES.find((g) => g.key === s.category) || GAUGES[2];
      const col = s.value == null ? COLORS.muted : colorOf(ref, s.value);
      const b = $("b", el);
      b.textContent = s.value == null ? "--" : `${fmt(s.value)}°`;
      b.style.color = col;
      const bar = $(".bar", el);
      bar.style.width = s.value == null ? "0%" : `${clamp((s.value - 20) / 90, 0, 1) * 100}%`;
      bar.style.background = col;
      bar.style.color = col;
    }
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
    const stale = state.lastDataAt && now - state.lastDataAt > STALE_MS;
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
        mode: "demo", source: "Browser simulator", interval: 1, readMs: 0, messages: [reason],
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

  const btnUnit = $("#btnUnit");
  btnUnit.textContent = "°" + settings.unit;
  btnUnit.onclick = () => {
    settings.unit = settings.unit === "C" ? "F" : "C";
    store.set("unit", settings.unit);
    btnUnit.textContent = "°" + settings.unit;
    rebuildAll();
  };
  const btnSound = $("#btnSound");
  btnSound.onclick = () => {
    settings.sound = !settings.sound;
    btnSound.textContent = settings.sound ? "ALARM ON" : "ALARM OFF";
    btnSound.classList.toggle("on", settings.sound);
    lastBeep = 0;
    if (settings.sound) { audio ||= new (window.AudioContext || window.webkitAudioContext)(); audio.resume?.(); }
  };
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
  $("#btnSettings").onclick = () => { fillSettings(); dlg.showModal(); };
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
    rebuildAll();
  });

  // Đồng hồ thời gian thực ở header
  function clockTick() {
    const d = new Date();
    $("#clock").textContent = d.toLocaleTimeString("vi-VN", { hour12: false });
    $("#date").textContent = d.toLocaleDateString("vi-VN", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }).toUpperCase();
  }
  clockTick(); setInterval(clockTick, 1000);
  setInterval(() => { for (const g of gauges) drawSpark(g); }, 500);

  let resizeTimer;
  new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => gauges.forEach(buildLayers), 80); }).observe($("#gauges"));
  window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`).addEventListener?.("change", () => gauges.forEach(buildLayers));

  gauges.forEach(buildLayers);
  requestAnimationFrame(frame);
  connect();
})();
