const state = {
  payload: null,
  rows: [],
  hoverItems: {},
};

const METRIC_OPTIONS = [
  ["max_oper_temp_c", "Max operative temperature (°C)"],
  ["overheating_hours_26", "Overheating hours >26°C"],
  ["overheating_hours_28", "Overheating hours >28°C"],
  ["night_overheating_hours_20", "Night overheating hours >20°C"],
  ["night_overheating_hours_26", "Night overheating hours >26°C"],
  ["degree_hours_above_26", "Degree-hours >26°C"],
  ["night_degree_hours_above_20", "Night degree-hours >20°C"],
  ["annual_cooling_kwh", "Annual cooling (kWh)"],
  ["summer_cooling_kwh", "Summer cooling (kWh)"],
  ["actual_peak_cooling_load_w", "Actual peak cooling load (W)"],
  ["actual_peak_heating_load_w", "Actual peak heating load (W)"],
];

const COLORS = {
  FRY: "#1d1d1f",
  XMY_seasonal: "#e30621",
  XMY_peak: "#f47b20",
  XMY_sustained: "#4aa381",
  XMY_nocturnal: "#7b61b8",
  external_benchmark: "#6f7f92",
  other: "#1f77b4",
};

function classify(name) {
  const s = String(name || "").toLowerCase();
  if (s === "fry" || s.includes("fry")) return "FRY";
  if (s.includes("season")) return "XMY_seasonal";
  if (s.includes("peak")) return "XMY_peak";
  if (s.includes("sustain") || s.includes("heatwave")) return "XMY_sustained";
  if (s.includes("noct") || s.includes("night")) return "XMY_nocturnal";
  if (s.includes("wehrli") || s.includes("1in10") || s.includes("1-in-10")) return "external_benchmark";
  return "other";
}

function colorFor(row) { return COLORS[classify(row.weather_file)] || COLORS.other; }
function labelForMetric(key) { return (METRIC_OPTIONS.find(d => d[0] === key) || [key, key])[1]; }
function finite(v) { const n = Number(v); return Number.isFinite(n) ? n : null; }
function fmt(v, digits = 2) { const n = finite(v); return n === null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits }); }

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width));
  const height = Math.max(260, Math.floor(rect.height));
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.style.width = width + "px";
  canvas.style.height = height + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function drawAxes(ctx, box, yMin, yMax, yLabel = "") {
  ctx.strokeStyle = "#dfe5ee";
  ctx.lineWidth = 1;
  ctx.font = "12px Source Sans 3, Helvetica Neue, Arial";
  ctx.fillStyle = "#7a8798";
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const t = i / ticks;
    const y = box.bottom - t * (box.bottom - box.top);
    ctx.beginPath(); ctx.moveTo(box.left, y); ctx.lineTo(box.right, y); ctx.stroke();
    const val = yMin + t * (yMax - yMin);
    ctx.fillText(fmt(val, 1), 8, y + 4);
  }
  ctx.strokeStyle = "#aeb8c6";
  ctx.beginPath(); ctx.moveTo(box.left, box.top); ctx.lineTo(box.left, box.bottom); ctx.lineTo(box.right, box.bottom); ctx.stroke();
  if (yLabel) ctx.fillText(yLabel, box.left, box.top - 8);
}

function tooltip(html, x, y) {
  const el = document.getElementById("tooltip");
  el.innerHTML = html;
  el.hidden = false;
  el.style.left = Math.min(window.innerWidth - 300, x + 14) + "px";
  el.style.top = Math.min(window.innerHeight - 120, y + 14) + "px";
}
function hideTooltip() { document.getElementById("tooltip").hidden = true; }

function populateSelects() {
  const metricSelect = document.getElementById("metricSelect");
  const scatterX = document.getElementById("scatterX");
  const scatterY = document.getElementById("scatterY");
  [metricSelect, scatterX, scatterY].forEach(sel => {
    sel.innerHTML = "";
    METRIC_OPTIONS.forEach(([key, label]) => {
      const opt = document.createElement("option"); opt.value = key; opt.textContent = label; sel.appendChild(opt);
    });
  });
  metricSelect.value = "overheating_hours_28";
  scatterX.value = "summer_cooling_kwh";
  scatterY.value = "overheating_hours_28";
}

function renderCards() {
  const cards = document.getElementById("cards");
  const worst = state.payload?.worst_cases || [];
  const pick = (metric) => worst.find(d => d.metric === metric) || {};
  const rows = [
    ["Worst max operative temp", pick("max_oper_temp_c").worst_value, "°C", pick("max_oper_temp_c").worst_weather_file],
    ["Worst OH >28°C", pick("overheating_hours_28").worst_value, "h", pick("overheating_hours_28").worst_weather_file],
    ["Worst night OH >26°C", pick("night_overheating_hours_26").worst_value, "h", pick("night_overheating_hours_26").worst_weather_file],
    ["Worst peak cooling", pick("actual_peak_cooling_load_w").worst_value, "W", pick("actual_peak_cooling_load_w").worst_weather_file],
  ];
  cards.innerHTML = rows.map(([label, value, unit, file]) => `
    <article class="card">
      <div class="label">${label}</div>
      <div class="value">${fmt(value, 1)} <span style="font-size:16px">${unit}</span></div>
      <div class="sub">${file || "—"}</div>
    </article>
  `).join("");
}

function renderLegend(elId, rows) {
  const el = document.getElementById(elId);
  const seen = new Set();
  el.innerHTML = rows.map(r => {
    const cls = classify(r.weather_file);
    if (seen.has(cls)) return "";
    seen.add(cls);
    return `<span class="legend-item"><span class="swatch" style="background:${COLORS[cls] || COLORS.other}"></span>${cls.replace(/_/g, " ")}</span>`;
  }).join("");
}

function drawBarChart() {
  const canvas = document.getElementById("barChart");
  const metric = document.getElementById("metricSelect").value;
  const { ctx, width, height } = setupCanvas(canvas);
  state.hoverItems.barChart = [];
  const rows = state.rows.filter(r => finite(r[metric]) !== null);
  const vals = rows.map(r => Number(r[metric]));
  if (!rows.length) { ctx.fillStyle = "#617086"; ctx.fillText("No data for this metric.", 28, 42); return; }
  const max = Math.max(...vals, 1);
  const box = { left: 74, right: width - 24, top: 36, bottom: height - 72 };
  drawAxes(ctx, box, 0, max * 1.08, labelForMetric(metric));
  const gap = 12;
  const barW = Math.max(18, (box.right - box.left - gap * (rows.length - 1)) / rows.length);
  rows.forEach((r, i) => {
    const v = Number(r[metric]);
    const x = box.left + i * (barW + gap);
    const h = (v / (max * 1.08)) * (box.bottom - box.top);
    const y = box.bottom - h;
    ctx.fillStyle = colorFor(r);
    ctx.fillRect(x, y, barW, h);
    ctx.fillStyle = "#152235";
    ctx.font = "11px Source Sans 3, Helvetica Neue, Arial";
    ctx.save(); ctx.translate(x + barW / 2, box.bottom + 8); ctx.rotate(-Math.PI / 5); ctx.textAlign = "right"; ctx.fillText(r.weather_file, 0, 0); ctx.restore();
    state.hoverItems.barChart.push({ x, y, w: barW, h, html: `<b>${r.weather_file}</b><br>${labelForMetric(metric)}: ${fmt(v, 2)}` });
  });
  renderLegend("barLegend", rows);
}

function drawScatter() {
  const canvas = document.getElementById("scatterChart");
  const xKey = document.getElementById("scatterX").value;
  const yKey = document.getElementById("scatterY").value;
  const { ctx, width, height } = setupCanvas(canvas);
  state.hoverItems.scatterChart = [];
  const rows = state.rows.filter(r => finite(r[xKey]) !== null && finite(r[yKey]) !== null);
  if (!rows.length) { ctx.fillStyle = "#617086"; ctx.fillText("No data for this combination.", 28, 42); return; }
  const xVals = rows.map(r => Number(r[xKey]));
  const yVals = rows.map(r => Number(r[yKey]));
  const xMin = Math.min(...xVals), xMax = Math.max(...xVals);
  const yMin = Math.min(...yVals), yMax = Math.max(...yVals);
  const pad = (a, b) => (b - a || Math.abs(b) || 1) * 0.08;
  const x0 = xMin - pad(xMin, xMax), x1 = xMax + pad(xMin, xMax);
  const y0 = yMin - pad(yMin, yMax), y1 = yMax + pad(yMin, yMax);
  const box = { left: 84, right: width - 28, top: 36, bottom: height - 64 };
  drawAxes(ctx, box, y0, y1, labelForMetric(yKey));
  ctx.fillStyle = "#7a8798"; ctx.fillText(labelForMetric(xKey), box.right - 200, box.bottom + 44);
  rows.forEach(r => {
    const x = box.left + ((Number(r[xKey]) - x0) / (x1 - x0)) * (box.right - box.left);
    const y = box.bottom - ((Number(r[yKey]) - y0) / (y1 - y0)) * (box.bottom - box.top);
    ctx.fillStyle = colorFor(r);
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();
    state.hoverItems.scatterChart.push({ x: x - 9, y: y - 9, w: 18, h: 18, html: `<b>${r.weather_file}</b><br>${labelForMetric(xKey)}: ${fmt(r[xKey], 2)}<br>${labelForMetric(yKey)}: ${fmt(r[yKey], 2)}` });
  });
  renderLegend("scatterLegend", rows);
}

function drawHeatmap() {
  const canvas = document.getElementById("heatmapChart");
  const { ctx, width, height } = setupCanvas(canvas);
  state.hoverItems.heatmapChart = [];
  const metrics = ["max_oper_temp_c", "overheating_hours_28", "night_overheating_hours_26", "degree_hours_above_26", "summer_cooling_kwh", "actual_peak_cooling_load_w"].filter(m => state.rows.some(r => finite(r[m]) !== null));
  const rows = state.rows;
  if (!rows.length || !metrics.length) { ctx.fillStyle = "#617086"; ctx.fillText("No heatmap data.", 28, 42); return; }
  const left = 150, top = 56, right = width - 24, bottom = height - 36;
  const cellW = (right - left) / metrics.length;
  const cellH = Math.min(42, (bottom - top) / rows.length);
  ctx.font = "12px Source Sans 3, Helvetica Neue, Arial";
  metrics.forEach((m, j) => {
    ctx.save(); ctx.translate(left + j * cellW + cellW / 2, top - 8); ctx.rotate(-Math.PI / 5); ctx.textAlign = "left"; ctx.fillStyle = "#617086"; ctx.fillText(labelForMetric(m), 0, 0); ctx.restore();
  });
  rows.forEach((r, i) => {
    ctx.fillStyle = "#152235"; ctx.textAlign = "right"; ctx.fillText(r.weather_file, left - 10, top + i * cellH + cellH * 0.65);
  });
  metrics.forEach((m, j) => {
    const vals = rows.map(r => finite(r[m])).filter(v => v !== null);
    const min = Math.min(...vals), max = Math.max(...vals);
    rows.forEach((r, i) => {
      const v = finite(r[m]);
      const norm = v === null ? 0 : (max === min ? 0.5 : (v - min) / (max - min));
      const alpha = 0.12 + norm * 0.78;
      const x = left + j * cellW, y = top + i * cellH;
      ctx.fillStyle = `rgba(227, 6, 33, ${alpha})`;
      ctx.fillRect(x + 2, y + 2, cellW - 4, cellH - 4);
      ctx.fillStyle = norm > 0.55 ? "#fff" : "#152235";
      ctx.textAlign = "center";
      ctx.fillText(fmt(v, 1), x + cellW / 2, y + cellH * 0.65);
      state.hoverItems.heatmapChart.push({ x: x + 2, y: y + 2, w: cellW - 4, h: cellH - 4, html: `<b>${r.weather_file}</b><br>${labelForMetric(m)}: ${fmt(v, 2)}<br>Normalized: ${fmt(norm, 2)}` });
    });
  });
}

function renderTable() {
  const table = document.getElementById("metricsTable");
  const cols = ["weather_file", "max_oper_temp_c", "overheating_hours_26", "overheating_hours_28", "night_overheating_hours_26", "degree_hours_above_26", "annual_cooling_kwh", "summer_cooling_kwh", "actual_peak_cooling_load_w"];
  table.innerHTML = `<thead><tr>${cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
    state.rows.map(r => `<tr>${cols.map(c => `<td>${c === "weather_file" ? r[c] : fmt(r[c], 2)}</td>`).join("")}</tr>`).join("") +
    `</tbody>`;
}

function attachHover(canvasId) {
  const canvas = document.getElementById(canvasId);
  canvas.onmousemove = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const item = (state.hoverItems[canvasId] || []).find(d => x >= d.x && x <= d.x + d.w && y >= d.y && y <= d.y + d.h);
    if (item) tooltip(item.html, ev.clientX, ev.clientY); else hideTooltip();
  };
  canvas.onmouseleave = hideTooltip;
}

function renderAll() {
  populateSelects();
  renderCards();
  drawBarChart();
  drawScatter();
  drawHeatmap();
  renderTable();
  attachHover("barChart");
  attachHover("scatterChart");
  attachHover("heatmapChart");
}

function loadPayload(payload) {
  state.payload = payload;
  state.rows = payload.summary || [];
  document.getElementById("status").textContent = `${state.rows.length} weather files loaded · ${payload.run_id || "run"}`;
  renderAll();
}

document.getElementById("jsonInput").addEventListener("change", async (ev) => {
  const file = ev.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  try { loadPayload(JSON.parse(text)); }
  catch (e) { document.getElementById("status").textContent = `Failed to parse JSON: ${e.message}`; }
});

document.getElementById("metricSelect").addEventListener("change", drawBarChart);
document.getElementById("scatterX").addEventListener("change", drawScatter);
document.getElementById("scatterY").addEventListener("change", drawScatter);
window.addEventListener("resize", () => { if (state.rows.length) requestAnimationFrame(renderAll); });

populateSelects();
