/* CH2025 Future Weather File Lab — frontend v4.2 */
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DEFAULT_COLORS = ["#0069b4", "#e20020", "#3a8c70", "#e07020", "#6a3090", "#5c6370"];
const VARIABLE_LABELS = {
  tas: ["Dry-bulb temperature", "°C"], hurs: ["Relative humidity", "%"], rsds: ["Global horizontal radiation", "W/m²"],
  sfcWind: ["Wind speed", "m/s"], windDir: ["Wind direction", "°"], pres: ["Atmospheric pressure", "Pa"],
  horizIR: ["Horizontal infrared radiation", "W/m²"], cloudcover: ["Cloud cover", "oktas / tenths"],
  dry_bulb_c: ["Dry-bulb temperature", "°C"], rh_pct: ["Relative humidity", "%"], ghi_wm2: ["Global horizontal radiation", "W/m²"],
  dhi_wm2: ["Diffuse horizontal irradiance", "W/m²"], dni_wm2: ["Direct normal irradiance", "W/m²"], dew_point_c: ["Dew point", "°C"]
};
const WEATHER_METRIC_LABELS = {
  annual_mean_tas: ["Annual mean temperature", "°C"], annual_max_tas: ["Annual maximum temperature", "°C"], annual_min_tas: ["Annual minimum temperature", "°C"],
  summer_mean_tas: ["Summer mean temperature", "°C"], summer_max_tas: ["Summer maximum temperature", "°C"], summer_cdh: ["Summer cooling degree hours", "CDH"],
  hot_day_count: ["Hot days", "days"], longest_hot_spell_days: ["Longest hot spell", "days"], tropical_night_count: ["Tropical nights", "nights"],
  longest_tropical_night_spell: ["Longest tropical-night spell", "nights"], annual_rsds_total: ["Annual radiation sum", "Wh/m²"], summer_rsds_total: ["Summer radiation sum", "Wh/m²"], annual_mean_hurs: ["Annual mean relative humidity", "%"]
};
const BPS_METRIC_LABELS = {
  annual_cooling_kwh: ["Annual cooling", "kWh"], summer_cooling_kwh: ["Summer cooling", "kWh"], peak_cooling_w: ["Peak cooling", "W"],
  overheating_hours: ["Overheating hours", "h"], max_operative_temp_c: ["Max operative temperature", "°C"], annual_heating_kwh: ["Annual heating", "kWh"]
};
const WEATHER_METRIC_DESCRIPTIONS = {
  row_count:                    ["Row count",                      "Total hourly rows in the file. A complete annual EPW has 8,760 rows."],
  annual_mean_tas:              ["Annual mean temperature",        "Average outdoor dry-bulb temperature across all 8,760 hours of the year."],
  annual_max_tas:               ["Annual maximum temperature",     "Single highest hourly temperature recorded anywhere in the year — the absolute peak."],
  annual_min_tas:               ["Annual minimum temperature",     "Single lowest hourly temperature in the year."],
  summer_mean_tas:              ["Summer mean temperature",        "Mean dry-bulb temperature during the summer evaluation period (typically JJA: June–August)."],
  summer_max_tas:               ["Summer maximum temperature",     "Peak hourly temperature reached during summer. Drives worst-case indoor temperature spikes."],
  summer_cdh:                   ["Summer cooling degree hours",    "Sum of hourly temperature exceedances above 26 °C during summer. Larger values mean heavier cumulative cooling demand on buildings."],
  hot_day_count:                ["Hot days",                       "Number of days with daily maximum temperature ≥ 30 °C (MeteoSwiss hot-day definition). Indicates how often peak heat thresholds are crossed."],
  longest_hot_spell_days:       ["Longest hot spell",              "Longest unbroken run of consecutive hot days (Tmax ≥ 30 °C). A 'spell' resets as soon as one cooler day interrupts the sequence. Long spells saturate building thermal mass."],
  tropical_night_count:         ["Tropical nights",                "Number of nights with minimum temperature ≥ 20 °C (MeteoSwiss tropical-night threshold). These nights prevent buildings from purging heat accumulated during the day."],
  longest_tropical_night_spell: ["Longest tropical-night spell",   "Longest unbroken run of consecutive tropical nights (Tmin ≥ 20 °C each night). A longer spell means the building cannot passively cool for multiple nights in a row."],
  annual_rsds_total:            ["Annual radiation sum",           "Total global horizontal solar radiation over the full year (Wh/m²). Governs solar heat gains through windows and opaque facades."],
  summer_rsds_total:            ["Summer radiation sum",           "Total solar radiation during summer (Wh/m²). High values amplify solar-driven overheating in glazed or poorly shaded spaces."],
  annual_mean_hurs:             ["Annual mean relative humidity",  "Annual average relative humidity (%). High humidity reduces the effectiveness of evaporative and ventilative cooling strategies."]
};
const YEAR_COLORS = {2001:"#6a8fb5",2003:"#e26060",2006:"#5aa07a",2007:"#c58c30",2011:"#8065b0",2014:"#4ea0b5",2015:"#c03060",2018:"#6c9a50",2019:"#9060a0"};
const state = { run:null, hitboxes:{}, weatherVar:null, weatherType:"bar", xmyProfile:null, xmyStat:null, bpsMetrics:[] };
const $ = id => document.getElementById(id);

function yearColor(y){ return YEAR_COLORS[y] || "#68758a"; }
function escHtml(v){ return String(v ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c])); }
function escAttr(v){ return escHtml(v); }
function fmt(v, digits=0){ if(v===null||v===undefined||Number.isNaN(Number(v))) return "–"; return Number(v).toLocaleString("en", {maximumFractionDigits:digits}); }
function labelFor(key, dict=VARIABLE_LABELS){ return dict[key]?.[0] || key.replaceAll("_", " "); }
function unitFor(key, dict=VARIABLE_LABELS){ return dict[key]?.[1] || ""; }
function isNum(v){ return v!==null && v!==undefined && v!=="" && !Number.isNaN(Number(v)); }

/* Station catalog */
async function loadStationCatalog(){
  const select = $("stationSelect"); if(!select) return;
  try{
    const res = await fetch("data/stations_catalog.json", {cache:"no-store"});
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json(); const stations = data.stations || [];
    if(!stations.length) return;
    const current = select.value || "SMA";
    select.innerHTML = stations.map(st => {
      const value = escAttr(st.value || st.id || st.code || "").toUpperCase();
      const label = escHtml(st.label || `${st.name || st.id} · ${value}`);
      return `<option value="${value}">${label}</option>`;
    }).join("");
    if([...select.options].some(o=>o.value===current)) select.value = current;
    updateScenarioLabel();
  } catch(err){ console.warn("Could not load station catalog; using fallback.", err); }
}

function updateScenarioLabel(){
  const station = $("stationSelect").value;
  const gwl = $("gwlSelect").value.toUpperCase().replace("GWL", "GWL ");
  $("scenarioLabel").textContent = `${station} · ${gwl}`;
  const mode = $("modeSelect").value;
  const profile = $("profileSelect").value.replaceAll("_", " ");
  $("methodLabel").textContent = mode === "fry" ?
    "FRY: FS ranking over tas / hurs / rsds; official tasmax/tasmin used only as tie-break." :
    `XMY stress profile: ${profile}; all profiles are selected from the same future candidate archive.`;
}

function buildCommandPlan(){
  const station = $("stationSelect").value.toLowerCase();
  const gwl = $("gwlSelect").value;
  const profile = $("profileSelect").value;
  const season = $("seasonSelect").value;
  const cmds = [
    `# Preview only: copy these commands into Terminal to run the backend.`,
    `python3 run_batch_pipeline_v4_1.py \\`,
    `  --stations ${station} \\`,
    `  --gwls ${gwl} \\`,
    `  --profiles ${profile} \\`,
    `  --continue-on-error`,
    ``,
    `# After the backend finishes, import:`,
    `# outputs/run_${station}_${gwl}/run_summary.json`
  ];
  $("commandBlock").textContent = cmds.join("\n");
  $("capabilityLevel").textContent = "Command preview";
  $("capabilityReason").textContent = "The browser cannot execute the backend. Run the previewed commands in Terminal, then import run_summary.json.";
  $("outputReadiness").textContent = "Preview only";
}

async function loadSampleRun(){
  try{ const res = await fetch("mock-data/sample_run_summary.json", {cache:"no-store"}); renderRun(await res.json()); }
  catch(err){ alert("Could not load sample run. Start the frontend with python3 -m http.server and check mock-data/sample_run_summary.json.\n\n" + err); }
}

function renderRun(data){
  state.run = data;
  $("capabilityLevel").textContent = data.station?.capability_level || "Unknown";
  $("capabilityReason").textContent = data.station?.capability_reason || "";
  $("scenarioLabel").textContent = `${data.station?.id || "?"} · ${data.scenario?.target_state || "?"}`;
  $("methodLabel").textContent = data.scenario?.selection_method || "";
  $("outputReadiness").textContent = (data.files || []).some(f => String(f.status||"").includes("pending") || String(f.status||"").includes("missing")) ? "Partly ready" : "Ready";
  renderFiles(data.files || []);
  renderSelectionProcess(data.selection_process || null, data.selection_cdf || null);
  renderSources(data.sources || [], data.assumptions || []);
  renderXmyCards(data.xmy_selection_cards || data.xmy_scores || []);
  setupWeatherControls(data);
  setupXmyControls(data);
  const bpsRows = data.evaluation_metrics || [];
  renderMetrics(bpsRows);
  setupSimulationControls(bpsRows);
  toggleBpsPanels(bpsRows);
  redrawAll();
}

/* Output files as compact accordion */
function renderFiles(files){
  const el = $("fileList");
  if(!files.length){ el.className="file-list empty-state"; el.textContent="No files loaded yet."; return; }
  el.className = "file-list file-accordion-list";
  const readyCount = files.filter(f => !(String(f.status||"").includes("pending") || String(f.status||"").includes("missing"))).length;
  const totalSize = files.reduce((s,f)=>s+(Number(f.size_kb)||0),0);
  el.innerHTML = `
    <details class="file-accordion">
      <summary><span><strong>${files.length} output files</strong> · ${readyCount} ready · ${fmt(totalSize)} KB</span><span class="accordion-hint">expand</span></summary>
      <div class="file-items-grid">${files.map(fileItemHtml).join("")}</div>
    </details>`;
}
function fileItemHtml(f){
  const status = String(f.status || "unknown");
  const pending = status.includes("pending") || status.includes("missing");
  const pillClass = pending ? "pill-pending" : "pill-ready";
  const href = f.download_href || f.href || "";
  const sizeTxt = f.size_kb ? `${Number(f.size_kb).toLocaleString()} KB` : "";
  const download = !pending && href ? `<a class="button button-download" href="${escAttr(href)}" download>↓ Download</a>` : "";
  return `<div class="file-item compact-file-item">
    <div class="file-item-left"><div class="file-top"><span class="file-type">${escHtml(f.type||"FILE")}</span><span class="file-label-text">${escHtml(f.label)}</span></div>
    <div class="file-path"><strong>Local:</strong> ${escHtml(f.path || "")}</div>${href ? `<div class="file-path"><strong>Served:</strong> ${escHtml(href)}</div>` : ""}
    <div class="file-meta"><span class="status-pill ${pillClass}">${escHtml(status)}</span>${sizeTxt?`<span class="file-size">${sizeTxt}</span>`:""}</div></div>
    <div class="file-item-right">${download}</div></div>`;
}

/* Selection CDF + compact table */
function renderSelectionProcess(proc, cdf){
  const el = $("selectionProcess");
  if(!proc || !proc.months?.length){ el.className="selection-panel empty-state"; el.textContent="Load a run JSON to inspect FS/CDF-based month selection."; return; }
  el.className = "selection-panel";
  const optionsMonth = proc.months.map((m,i)=>`<option value="${i+1}">${escHtml(m.month)} · ${m.selected_year}</option>`).join("");
  const variableOptions = (cdf?.variables || ["tas","hurs","rsds"]).map(v=>`<option value="${v}">${labelFor(v)}</option>`).join("");
  el.innerHTML = `
    <div class="selection-controls chart-toolbar">
      <label>Month <select id="cdfMonthSelect">${optionsMonth}</select></label>
      <label>Variable <select id="cdfVariableSelect">${variableOptions}</select></label>
    </div>
    <p class="section-note">This CDF plot follows the logic of the FS statistic: the selected candidate month should be close to the CH2025 target distribution. Lower CDF distance means better representativeness.</p>
    <canvas id="cdfChart" height="280" style="width:100%"></canvas>
    <p class="data-provenance-note">CDF data are read from the generated candidate daily summary and CH2025 target files; no synthetic CDF is drawn.</p>
    <details class="compact-details"><summary>Show selected month table</summary>${selectionTableHtml(proc)}</details>`;
  $("cdfMonthSelect").addEventListener("change", () => drawCDFChart("cdfChart"));
  $("cdfVariableSelect").addEventListener("change", () => drawCDFChart("cdfChart"));
}
function selectionTableHtml(proc){
  const rows = proc.months.map(m => `<tr><td><strong>${escHtml(m.month)}</strong></td><td><span class="year-pill" style="background:${yearColor(m.selected_year)}22;color:${yearColor(m.selected_year)};border:1px solid ${yearColor(m.selected_year)}55">${m.selected_year}</span></td><td>${escHtml(m.model_chain || "")}</td><td>${isNum(m.fs_score)?Number(m.fs_score).toFixed(4):"–"}</td><td>${escHtml(m.tas_delta || "")}</td></tr>`).join("");
  return `<table class="selection-table"><thead><tr><th>Month</th><th>Year</th><th>Model chain</th><th>FS score</th><th>Tie-break deviation</th></tr></thead><tbody>${rows}</tbody></table>`;
}

/* Weather variable controls */
function setupWeatherControls(data){
  const select = $("weatherVariableSelect");
  const chartType = $("weatherChartType");
  const vars = data.monthly_variables || inferMonthlyVars(data);
  const keys = Object.keys(vars);
  select.innerHTML = keys.map(k=>`<option value="${k}">${escHtml(vars[k].label || labelFor(k))}</option>`).join("");
  state.weatherVar = keys.includes(state.weatherVar) ? state.weatherVar : (keys[0] || null);
  if(state.weatherVar) select.value = state.weatherVar;
  chartType.value = state.weatherType || "bar";
  select.onchange = () => { state.weatherVar = select.value; drawWeatherVariableChart(); };
  chartType.onchange = () => { state.weatherType = chartType.value; drawWeatherVariableChart(); };
}
function inferMonthlyVars(data){
  const out = {};
  if(data.monthly_temperature?.length) out.tas = {label:"Dry-bulb temperature", unit:"°C", selected:data.monthly_temperature, reference:data.monthly_temperature_reference || []};
  if(data.monthly_radiation?.length) out.rsds = {label:"Global horizontal radiation", unit:"W/m²", selected:data.monthly_radiation, reference:[]};
  return out;
}

/* XMY controls */
function setupXmyControls(data){
  const psel = $("xmyProfileSelect"), ssel = $("xmyStatSelect");
  const rows = data.weather_diagnostics || [];
  const profiles = rows.map(r=>r.label || r.file).filter(Boolean);
  const numericKeys = rows.length ? Object.keys(rows[0]).filter(k => k !== "label" && k !== "file" && k !== "path" && rows.some(r=>isNum(r[k]))) : [];
  psel.innerHTML = profiles.map(p=>`<option value="${escAttr(p)}">${escHtml(p)}</option>`).join("");
  ssel.innerHTML = numericKeys.map(k=>`<option value="${k}">${escHtml(labelFor(k, WEATHER_METRIC_LABELS))}</option>`).join("");
  state.xmyProfile = profiles.includes(state.xmyProfile) ? state.xmyProfile : (profiles[0] || null);
  state.xmyStat = numericKeys.includes(state.xmyStat) ? state.xmyStat : (numericKeys.includes("summer_cdh") ? "summer_cdh" : numericKeys[0]);
  if(state.xmyProfile) psel.value = state.xmyProfile;
  if(state.xmyStat) ssel.value = state.xmyStat;
  psel.onchange = () => { state.xmyProfile = psel.value; drawXmyStatChart(); };
  ssel.onchange = () => { state.xmyStat = ssel.value; drawXmyStatChart(); updateXmyStatDescription(ssel.value); };
}
function renderXmyCards(scores){
  const el = $("xmyCards");
  if(!scores.length){ el.className="xmy-card-grid empty-state"; el.textContent="No XMY profiles loaded yet."; return; }
  el.className = "xmy-card-grid";
  el.innerHTML = scores.map((s,i)=>`<div class="xmy-card" title="${escAttr(s.objective || "")}"><p>${escHtml(s.label || s.profile)}</p><strong>${fmt(s.score,1)}</strong><span>${escHtml(s.unit || "")}</span><small>${s.criterion_type ? `${escHtml(s.criterion_type)}<br>` : ""}${s.metric ? `Metric: ${escHtml(s.metric)}<br>` : ""}${s.source_year ? `Year ${s.source_year}` : ""}${s.model_chain ? ` · ${escHtml(s.model_chain)}` : ""}</small></div>`).join("");
}

function renderMetrics(rows){
  const el = $("metricsTable");
  if(!rows.length){ el.className="table-wrap empty-state"; el.textContent="No BPS metrics loaded yet. Run the Honeybee/OpenStudio batch layer and rebuild run_summary.json to populate this panel."; return; }
  el.className = "table-wrap";
  const keys = Object.keys(rows[0]).filter(k=>k!="file"&&k!="label"&&rows.some(r=>isNum(r[k])));
  el.innerHTML = `<table><thead><tr><th>File</th>${keys.map(k=>`<th>${escHtml(labelFor(k,BPS_METRIC_LABELS))}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr><td>${escHtml(r.file || r.label || "")}</td>${keys.map(k=>`<td>${fmt(r[k],1)} ${unitFor(k,BPS_METRIC_LABELS)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function setupSimulationControls(rows){
  const el = $("simulationControls");
  if(!rows.length){ el.className="metric-checkboxes empty-state"; el.textContent="No BPS results loaded yet."; drawEmptyChart("simulationChart", "No BPS results loaded"); return; }
  const keys = Object.keys(rows[0]).filter(k=>k!=="file"&&k!=="label"&&rows.some(r=>isNum(r[k])));
  state.bpsMetrics = state.bpsMetrics.length ? state.bpsMetrics.filter(k=>keys.includes(k)) : keys.slice(0, Math.min(3, keys.length));
  el.className="metric-checkboxes";
  el.innerHTML = keys.map(k=>`<label><input type="checkbox" value="${k}" ${state.bpsMetrics.includes(k)?"checked":""}> ${escHtml(labelFor(k,BPS_METRIC_LABELS))}</label>`).join("");
  el.querySelectorAll("input").forEach(cb=> cb.addEventListener("change", () => { state.bpsMetrics = [...el.querySelectorAll("input:checked")].map(x=>x.value); drawSimulationChart(); }));
}

function renderSources(sources, assumptions){
  const el = $("sourceList");
  if(!sources.length){ el.className="source-list empty-state"; el.textContent="No sources loaded yet."; }
  else { el.className="source-list"; el.innerHTML = sources.map(s=>`<div class="source-item"><a href="${escAttr(s.url||"#")}" target="_blank" rel="noreferrer">${escHtml(s.name)}</a><p>${escHtml(s.description||"")}</p></div>`).join(""); }
  $("assumptionList").innerHTML = assumptions.map(a=>`<li>${escHtml(a)}</li>`).join("");
}

/* Canvas helpers + tooltip */
function setupCanvas(id){
  const canvas=$(id);
  if(!canvas) throw new Error(`Missing canvas #${id}`);
  const dpr=window.devicePixelRatio||1;
  const parent=canvas.parentElement;
  // Reset CSS width before measuring so dropdown redraws cannot lock an old pixel width.
  canvas.style.width="100%";
  const parentRect = parent ? parent.getBoundingClientRect() : null;
  const measuredW = parentRect?.width || canvas.getBoundingClientRect().width || 600;
  const w=Math.max(320, Math.floor(measuredW));
  // FIX: canvas.height = N*dpr also updates canvas.getAttribute("height"), so every
  // subsequent call would read the DPR-multiplied value and double the height again.
  // We cache the original logical height in data-logical-height on the first call and
  // always read from there, ignoring the attribute and computed style after that.
  if(!canvas.dataset.logicalHeight){
    const rawAttr = parseInt(canvas.getAttribute("height"), 10);
    const rawCss  = parseInt(getComputedStyle(canvas).height, 10);
    canvas.dataset.logicalHeight = String(Math.max(160, rawAttr || rawCss || 220));
  }
  const h = parseInt(canvas.dataset.logicalHeight, 10);
  canvas.style.height=h+"px";
  canvas.width=Math.round(w*dpr);
  canvas.height=Math.round(h*dpr);
  const ctx=canvas.getContext("2d");
  ctx.setTransform(dpr,0,0,dpr,0,0);
  state.hitboxes[id]=[];
  canvas.onmousemove = ev => handleTooltip(id, ev);
  canvas.onmouseleave = hideTooltip;
  return {ctx,w,h};
}
function addHit(id, x,y,w,h, html){ state.hitboxes[id].push({x,y,w,h,html}); }
function handleTooltip(id, ev){
  const rect = $(id).getBoundingClientRect(); const x=ev.clientX-rect.left, y=ev.clientY-rect.top; const hit=(state.hitboxes[id]||[]).find(b=>x>=b.x&&x<=b.x+b.w&&y>=b.y&&y<=b.y+b.h);
  const tip=$("chartTooltip"); if(!hit){ hideTooltip(); return; }
  tip.hidden=false; tip.innerHTML=hit.html; tip.style.left=(ev.clientX+12)+"px"; tip.style.top=(ev.clientY+12)+"px";
}
function hideTooltip(){ const tip=$("chartTooltip"); if(tip) tip.hidden=true; }
function drawEmpty(ctx,w,h,msg="No data loaded"){ ctx.clearRect(0,0,w,h); ctx.fillStyle="#9aa3af"; ctx.font="13px Source Sans 3, Helvetica, Arial"; ctx.textAlign="center"; ctx.fillText(msg,w/2,h/2+5); }
function drawEmptyChart(id,msg){ const {ctx,w,h}=setupCanvas(id); drawEmpty(ctx,w,h,msg); }

function drawWeatherVariableChart(){
  const data=state.run; if(!data){ drawEmptyChart("weatherVariableChart","No data loaded"); return; }
  const vars = data.monthly_variables || inferMonthlyVars(data); const key=state.weatherVar || Object.keys(vars)[0]; const item=vars[key];
  if(!item){ drawEmptyChart("weatherVariableChart","No monthly variable available"); return; }
  $("weatherChartTitle").textContent = `${item.label || labelFor(key)}${item.unit?` (${item.unit})`:""}`;
  let series = Array.isArray(item.series) && item.series.length ? item.series : [];
  if(!series.length){
    if((item.reference||[]).length) series.push({label:"Reference 1991–2020", role:"reference", values:item.reference});
    if((item.selected||[]).length) series.push({label:"FRY / selected file", role:"generated", values:item.selected});
  }
  series = series.filter(s => (s.values||[]).some(isNum));
  if(!series.length){ drawEmptyChart("weatherVariableChart", "No monthly values for selected variable"); $("weatherLegend").innerHTML=""; return; }
  const colors = series.map((s,i)=>seriesColor(s,i));
  if((state.weatherType||"bar") === "line") drawMultiLineComparison("weatherVariableChart", MONTHS, series, item.unit || "", colors);
  else drawMultiBarComparison("weatherVariableChart", MONTHS, series, item.unit || "", colors);
  $("weatherLegend").innerHTML = series.map((s,i)=>`<span class="legend-item"><span class="legend-swatch" style="background:${colors[i]}"></span>${escHtml(s.label || `Series ${i+1}`)}${s.role==="external_benchmark"?" · external benchmark":""}</span>`).join("");
}
function drawBarComparison(id, labels, refVals, selVals, unit, refName, selName){
  const {ctx,w,h}=setupCanvas(id); const vals=[...(refVals||[]), ...(selVals||[])].filter(isNum).map(Number); if(!vals.length){drawEmpty(ctx,w,h,"No monthly data");return;}
  const PAD={l:48,r:14,t:18,b:34}; const plotW=w-PAD.l-PAD.r, plotH=h-PAD.t-PAD.b; const minRaw=Math.min(...vals), maxRaw=Math.max(...vals); const vMin=Math.min(0, Math.floor(minRaw-1)); const vMax=Math.ceil(maxRaw*1.08+1);
  const y=v=>PAD.t+plotH-((v-vMin)/(vMax-vMin))*plotH; const xStep=plotW/labels.length; const zeroY=y(0);
  ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="right";
  for(let i=0;i<=4;i++){ const v=vMin+(vMax-vMin)*i/4; const yy=y(v); ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke(); ctx.fillText(fmt(v,0),PAD.l-5,yy+4); }
  const hasRef=(refVals||[]).some(isNum); const bw=xStep*(hasRef?0.26:0.42);
  labels.forEach((lab,i)=>{ const cx=PAD.l+(i+0.5)*xStep; if(hasRef&&isNum(refVals[i])){ const v=Number(refVals[i]); const top=Math.min(y(v),zeroY), bh=Math.abs(y(v)-zeroY)||2; ctx.fillStyle="#aac5d8"; ctx.fillRect(cx-bw-3,top,bw,bh); addHit(id,cx-bw-3,top,bw,bh,`${lab}<br>${refName}: <b>${fmt(v,2)} ${unit}</b>`); }
    if(isNum(selVals[i])){ const v=Number(selVals[i]); const top=Math.min(y(v),zeroY), bh=Math.abs(y(v)-zeroY)||2; ctx.fillStyle="#e20020"; ctx.fillRect(cx+(hasRef?3:-bw/2),top,bw,bh); addHit(id,cx+(hasRef?3:-bw/2),top,bw,bh,`${lab}<br>${selName}: <b>${fmt(v,2)} ${unit}</b>`); }
    ctx.fillStyle="#5c6370"; ctx.textAlign="center"; ctx.fillText(lab,cx,h-PAD.b+16); });
}
function drawLineComparison(id, labels, refVals, selVals, unit, refName, selName){
  const {ctx,w,h}=setupCanvas(id); const vals=[...(refVals||[]), ...(selVals||[])].filter(isNum).map(Number); if(!vals.length){drawEmpty(ctx,w,h,"No monthly data");return;}
  const PAD={l:48,r:16,t:18,b:34}; const plotW=w-PAD.l-PAD.r, plotH=h-PAD.t-PAD.b; const vMin=Math.floor(Math.min(...vals)-1), vMax=Math.ceil(Math.max(...vals)+1); const y=v=>PAD.t+plotH-((v-vMin)/(vMax-vMin))*plotH; const x=i=>PAD.l+(i+0.5)*plotW/labels.length;
  ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="right"; for(let i=0;i<=4;i++){ const v=vMin+(vMax-vMin)*i/4; const yy=y(v); ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke(); ctx.fillText(fmt(v,0),PAD.l-5,yy+4); }
  function series(vals,color,name){ ctx.strokeStyle=color; ctx.lineWidth=2.5; ctx.beginPath(); vals.forEach((v,i)=>{ if(!isNum(v))return; const xx=x(i), yy=y(Number(v)); if(i===0)ctx.moveTo(xx,yy); else ctx.lineTo(xx,yy); }); ctx.stroke(); vals.forEach((v,i)=>{ if(!isNum(v))return; const xx=x(i), yy=y(Number(v)); ctx.fillStyle=color; ctx.beginPath();ctx.arc(xx,yy,4,0,Math.PI*2);ctx.fill(); addHit(id,xx-8,yy-8,16,16,`${labels[i]}<br>${name}: <b>${fmt(v,2)} ${unit}</b>`); }); }
  if((refVals||[]).some(isNum)) series(refVals,"#aac5d8",refName); series(selVals,"#e20020",selName); ctx.fillStyle="#5c6370"; ctx.textAlign="center"; labels.forEach((lab,i)=>ctx.fillText(lab,x(i),h-PAD.b+16));
}

function seriesColor(s,i){
  if(s.role === "reference") return "#aac5d8";
  if(s.role === "generated") return "#e20020";
  if(s.role === "external_benchmark") return "#6a3090";
  return DEFAULT_COLORS[i % DEFAULT_COLORS.length];
}
function numericExtentForSeries(series){
  const vals=[]; series.forEach(s => (s.values||[]).forEach(v=>{ if(isNum(v)) vals.push(Number(v)); }));
  if(!vals.length) return [0,1];
  let min=Math.min(...vals), max=Math.max(...vals);
  if(min===max){ min-=1; max+=1; }
  const pad=(max-min)*0.08 || 1;
  return [Math.min(0,min-pad), max+pad];
}
function drawMultiBarComparison(id, labels, series, unit, colors){
  const {ctx,w,h}=setupCanvas(id); const [vMin,vMax]=numericExtentForSeries(series); if(vMax<=vMin){drawEmpty(ctx,w,h,"No monthly data");return;}
  const PAD={l:50,r:14,t:18,b:36}; const plotW=w-PAD.l-PAD.r, plotH=h-PAD.t-PAD.b; const y=v=>PAD.t+plotH-((v-vMin)/(vMax-vMin))*plotH; const xStep=plotW/labels.length; const zeroY=y(0);
  ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="right";
  for(let i=0;i<=4;i++){ const v=vMin+(vMax-vMin)*i/4; const yy=y(v); ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke(); ctx.fillText(fmt(v,0),PAD.l-5,yy+4); }
  const groupW=xStep*.78, bw=Math.max(3, groupW/series.length-2);
  labels.forEach((lab,i)=>{ const baseX=PAD.l+i*xStep+(xStep-groupW)/2; series.forEach((s,j)=>{ const v=(s.values||[])[i]; if(!isNum(v))return; const val=Number(v), top=Math.min(y(val),zeroY), bh=Math.max(2,Math.abs(y(val)-zeroY)); const xx=baseX+j*(groupW/series.length); ctx.fillStyle=colors[j]; ctx.fillRect(xx,top,bw,bh); addHit(id,xx,top,bw,bh,`${lab}<br>${escHtml(s.label||`Series ${j+1}`)}: <b>${fmt(val,2)} ${unit}</b>`); }); ctx.fillStyle="#5c6370"; ctx.textAlign="center"; ctx.fillText(lab,PAD.l+(i+.5)*xStep,h-PAD.b+17); });
}
function drawMultiLineComparison(id, labels, series, unit, colors){
  const {ctx,w,h}=setupCanvas(id); const [vMin,vMax]=numericExtentForSeries(series); const PAD={l:50,r:16,t:18,b:36}; const plotW=w-PAD.l-PAD.r, plotH=h-PAD.t-PAD.b; const y=v=>PAD.t+plotH-((v-vMin)/(vMax-vMin))*plotH; const x=i=>PAD.l+(i+0.5)*plotW/labels.length;
  ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="right"; for(let i=0;i<=4;i++){ const v=vMin+(vMax-vMin)*i/4; const yy=y(v); ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke(); ctx.fillText(fmt(v,0),PAD.l-5,yy+4); }
  series.forEach((s,j)=>{ const vals=s.values||[]; ctx.strokeStyle=colors[j]; ctx.lineWidth=2.4; ctx.beginPath(); let started=false; vals.forEach((v,i)=>{ if(!isNum(v)){started=false; return;} const xx=x(i), yy=y(Number(v)); if(!started){ctx.moveTo(xx,yy); started=true;} else ctx.lineTo(xx,yy); }); ctx.stroke(); vals.forEach((v,i)=>{ if(!isNum(v))return; const xx=x(i), yy=y(Number(v)); ctx.fillStyle=colors[j]; ctx.beginPath();ctx.arc(xx,yy,4,0,Math.PI*2);ctx.fill(); addHit(id,xx-8,yy-8,16,16,`${labels[i]}<br>${escHtml(s.label||`Series ${j+1}`)}: <b>${fmt(v,2)} ${unit}</b>`); }); });
  ctx.fillStyle="#5c6370"; ctx.textAlign="center"; labels.forEach((lab,i)=>ctx.fillText(lab,x(i),h-PAD.b+17));
}

function drawCDFChart(id){
  const data=state.run?.selection_cdf;
  if(!data){ drawEmptyChart(id,"No real CDF data available. Rebuild run_summary with --candidate-daily-summary and target CH2025 files."); return; }
  const m = $("cdfMonthSelect")?.value || "1";
  const v = $("cdfVariableSelect")?.value || "tas";
  const pack=data.months?.[m]?.[v];
  if(!pack || !pack.target?.x?.length || !pack.selected?.x?.length){ drawEmptyChart(id,"No real CDF data for this month / variable"); return; }
  const {ctx,w,h}=setupCanvas(id); const PAD={l:52,r:18,t:24,b:46}; const plotW=w-PAD.l-PAD.r, plotH=h-PAD.t-PAD.b;
  const allX=[]; [pack.target, pack.selected, ...(pack.alternatives||[])].forEach(s => (s?.x||[]).forEach(x=>isNum(x)&&allX.push(Number(x)))); if(!allX.length){ drawEmpty(ctx,w,h,"Empty CDF series"); return; }
  let xmin=Math.min(...allX), xmax=Math.max(...allX); if(xmin===xmax){ xmin-=1; xmax+=1; }
  const xScale=x=>PAD.l+((x-xmin)/(xmax-xmin))*plotW; const yScale=y=>PAD.t+plotH-y*plotH;
  ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="right"; [0,0.25,0.5,0.75,1].forEach(t=>{const yy=yScale(t);ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke();ctx.fillText(t.toFixed(2),PAD.l-5,yy+4);});
  ctx.textAlign="center"; for(let i=0;i<=4;i++){ const xv=xmin+(xmax-xmin)*i/4; ctx.fillText(fmt(xv,1),xScale(xv),h-PAD.b+18); }
  function drawSeries(s,color,width,dashed=false,label=""){
    if(!s?.x?.length)return;
    ctx.strokeStyle=color; ctx.lineWidth=width; ctx.setLineDash(dashed?[5,4]:[]); ctx.beginPath();
    s.x.forEach((xx,i)=>{ const X=xScale(Number(xx)), Y=yScale(Number(s.y[i])); if(i===0)ctx.moveTo(X,Y); else ctx.lineTo(X,Y); });
    ctx.stroke(); ctx.setLineDash([]);
    // sparse hitboxes along the line so hover still reports which CDF is being shown
    const n=s.x.length; const step=Math.max(1,Math.floor(n/20));
    for(let i=0;i<n;i+=step){ const X=xScale(Number(s.x[i])), Y=yScale(Number(s.y[i])); addHit(id,X-5,Y-5,10,10,`${escHtml(label||s.label||"CDF")}<br>x: <b>${fmt(s.x[i],2)}</b><br>CDF: <b>${fmt(s.y[i],2)}</b>`); }
  }
  (pack.alternatives||[]).slice(0,3).forEach((s,i)=>drawSeries(s, ["#c58c30","#8065b0","#5aa07a"][i],1.6,true,s.label));
  drawSeries(pack.target,"#111",2.8,false,pack.target.label||"CH2025 target"); drawSeries(pack.selected,"#e20020",3,false,pack.selected.label||"Selected candidate");
  ctx.fillStyle="#1a1a1a"; ctx.textAlign="left"; ctx.font="12px Source Sans 3, Helvetica"; const unit=unitFor(v); ctx.fillText(`${MONTHS[Number(m)-1]} · ${labelFor(v)}${unit?` (${unit})`:""}`, PAD.l, 16);
  const panel=$(id)?.parentElement;
  let legend=panel?.querySelector(".cdf-legend");
  if(panel && !legend){ legend=document.createElement("div"); legend.className="cdf-legend"; panel.appendChild(legend); }
  if(legend){ legend.innerHTML=`<span style="color:#111"><span class="line"></span>CH2025 target</span><span style="color:#e20020"><span class="line"></span>Selected candidate</span><span style="color:#8065b0"><span class="line dash"></span>Shortlisted alternatives</span>`; }
}
function drawCarpetPlot(id, monthlyMeans, amplitudes, matrix){
  // Only draw from real hourly data in run_summary.json. Earlier versions used a synthetic
  // diurnal fallback; that is intentionally removed to avoid misleading diagnostics.
  if(!matrix || !Array.isArray(matrix) || !matrix.some(row => Array.isArray(row) && row.some(isNum))){
    drawEmptyChart(id,"No real hourly temperature matrix loaded");
    const leg=$("carpetLegend"); if(leg) leg.innerHTML="";
    return;
  }
  const flat=matrix.flat().filter(isNum).map(Number);
  const tMin=Math.min(...flat), tMax=Math.max(...flat);
  const {ctx,w,h}=setupCanvas(id); const PAD_L=46, PAD_R=12, PAD_T=10, PAD_B=30; const plotW=w-PAD_L-PAD_R, plotH=h-PAD_T-PAD_B; const cellW=plotW/12, cellH=plotH/24;
  for(let m=0;m<12;m++){
    for(let hr=0;hr<24;hr++){
      const t=matrix[m]?.[hr];
      if(!isNum(t)) continue;
      ctx.fillStyle=tempColor(Number(t),tMin,tMax);
      ctx.fillRect(PAD_L+m*cellW,PAD_T+hr*cellH,cellW+0.5,cellH+0.5);
      addHit(id,PAD_L+m*cellW,PAD_T+hr*cellH,cellW,cellH,`${MONTHS[m]} · ${String(hr).padStart(2,"0")}:00<br><b>${fmt(t,1)} °C</b>`);
    }
  }
  ctx.fillStyle="#5c6370"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="center"; MONTHS.forEach((lab,m)=>ctx.fillText(lab,PAD_L+(m+0.5)*cellW,h-PAD_B+14)); ctx.textAlign="right"; [0,6,12,18,23].forEach(hr=>ctx.fillText(`${String(hr).padStart(2,"0")}:00`,PAD_L-5,PAD_T+hr*cellH+cellH/2+4));
  const leg=$("carpetLegend"); if(leg){ const items=[0,.2,.4,.6,.8,1].map(k=>{ const t=tMin+k*(tMax-tMin); return `<span class="legend-item"><span style="display:inline-block;width:20px;height:10px;background:${tempColor(t,tMin,tMax)}"></span>${fmt(t,0)}°C</span>`; }).join(""); leg.innerHTML=`<div class="chart-legend">${items}</div>`; }
}
function tempColor(t,tMin,tMax){ const r=Math.max(0,Math.min(1,(t-tMin)/(tMax-tMin||1))); function L(a,b,k){return Math.round(a+(b-a)*k);} if(r<0.5){const k=r*2; return `rgb(${L(30,255,k)},${L(90,255,k)},${L(200,255,k)})`;} const k=(r-.5)*2; return `rgb(${L(255,200,k)},${L(255,40,k)},${L(255,30,k)})`; }

function drawXmyStatChart(){
  const rows=state.run?.weather_diagnostics || []; const key=state.xmyStat; if(!rows.length||!key){ drawEmptyChart("xmyStatChart","No weather-file statistics loaded"); return; }
  const {ctx,w,h}=setupCanvas("xmyStatChart"); const PAD={l:54,r:18,t:24,b:58}; const vals=rows.map(r=>Number(r[key])).filter(v=>!Number.isNaN(v)); if(!vals.length){ drawEmpty(ctx,w,h,"Selected statistic is not numeric"); return; }
  const max=Math.max(...vals)*1.12 || 1; const y=v=>PAD.t+(h-PAD.t-PAD.b)-(v/max)*(h-PAD.t-PAD.b); const step=(w-PAD.l-PAD.r)/rows.length; const bw=step*.55; ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.textAlign="right"; ctx.font="11px Source Sans 3, Helvetica"; [0,.25,.5,.75,1].forEach(fr=>{const yy=y(max*fr);ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke();ctx.fillText(fmt(max*fr,0),PAD.l-5,yy+4);});
  rows.forEach((r,i)=>{ const v=Number(r[key]); const cx=PAD.l+(i+.5)*step; const yy=y(v); ctx.fillStyle=DEFAULT_COLORS[i%DEFAULT_COLORS.length]; ctx.fillRect(cx-bw/2,yy,bw,h-PAD.b-yy); addHit("xmyStatChart",cx-bw/2,yy,bw,h-PAD.b-yy,`${escHtml(r.label||r.file)}<br>${labelFor(key,WEATHER_METRIC_LABELS)}: <b>${fmt(v,1)} ${unitFor(key,WEATHER_METRIC_LABELS)}</b>`); ctx.fillStyle="#5c6370"; ctx.save();ctx.translate(cx,h-PAD.b+16);ctx.rotate(-0.42);ctx.textAlign="right";ctx.fillText(String(r.label||r.file).replaceAll("_"," "),0,0);ctx.restore(); });
  const panel=$("xmyStatChart")?.parentElement; let legend=panel?.querySelector(".xmy-stat-legend"); if(panel && !legend){ legend=document.createElement("div"); legend.className="chart-legend xmy-stat-legend"; panel.appendChild(legend); }
  if(legend){ legend.innerHTML=rows.map((r,i)=>`<span class="legend-item"><span class="legend-swatch" style="background:${DEFAULT_COLORS[i%DEFAULT_COLORS.length]}"></span>${escHtml(r.label||r.file)}</span>`).join(""); }
  updateXmyStatDescription(key);
}
function updateXmyStatDescription(key){
  const canvas = $("xmyStatChart");
  if(!canvas) return;
  const entry = WEATHER_METRIC_DESCRIPTIONS[key];
  let el = canvas.parentElement?.querySelector(".metric-desc-note");
  if(!el){
    el = document.createElement("div");
    el.className = "metric-desc-note";
    canvas.insertAdjacentElement("afterend", el);
  }
  if(entry){
    el.hidden = false;
    el.innerHTML = `<span class="desc-key">${escHtml(entry[0])}:</span><span class="desc-text">${escHtml(entry[1])}</span>`;
  } else {
    el.hidden = true;
  }
}
function drawSimulationChart(){
  const rows=state.run?.evaluation_metrics || []; const keys=state.bpsMetrics||[]; if(!rows.length||!keys.length){ drawEmptyChart("simulationChart","No BPS results loaded"); return; }
  const {ctx,w,h}=setupCanvas("simulationChart"); const PAD={l:58,r:18,t:24,b:64}; const vals=[]; rows.forEach(r=>keys.forEach(k=>isNum(r[k])&&vals.push(Number(r[k])))); const max=Math.max(...vals)*1.12||1; const y=v=>PAD.t+(h-PAD.t-PAD.b)-(v/max)*(h-PAD.t-PAD.b); const step=(w-PAD.l-PAD.r)/rows.length; const groupW=step*.7; const bw=groupW/keys.length;
  ctx.strokeStyle="#e8ecf0"; ctx.fillStyle="#8b96a6"; ctx.font="11px Source Sans 3, Helvetica"; ctx.textAlign="right"; [0,.25,.5,.75,1].forEach(fr=>{const yy=y(max*fr);ctx.beginPath();ctx.moveTo(PAD.l,yy);ctx.lineTo(w-PAD.r,yy);ctx.stroke();ctx.fillText(fmt(max*fr,0),PAD.l-5,yy+4);});
  rows.forEach((r,i)=>{ const cx=PAD.l+(i+.5)*step; keys.forEach((k,j)=>{ if(!isNum(r[k]))return; const v=Number(r[k]); const xx=cx-groupW/2+j*bw; const yy=y(v); ctx.fillStyle=DEFAULT_COLORS[j%DEFAULT_COLORS.length]; ctx.fillRect(xx,yy,bw-2,h-PAD.b-yy); addHit("simulationChart",xx,yy,bw-2,h-PAD.b-yy,`${escHtml(r.file||r.label)}<br>${labelFor(k,BPS_METRIC_LABELS)}: <b>${fmt(v,1)} ${unitFor(k,BPS_METRIC_LABELS)}</b>`); }); ctx.fillStyle="#5c6370"; ctx.save();ctx.translate(cx,h-PAD.b+18);ctx.rotate(-0.35);ctx.textAlign="right";ctx.fillText(String(r.file||r.label),0,0);ctx.restore(); });
  const panel=$("simulationChart")?.parentElement; let legend=panel?.querySelector(".simulation-legend"); if(panel && !legend){ legend=document.createElement("div"); legend.className="chart-legend simulation-legend"; panel.appendChild(legend); }
  if(legend){ legend.innerHTML=keys.map((k,j)=>`<span class="legend-item"><span class="legend-swatch" style="background:${DEFAULT_COLORS[j%DEFAULT_COLORS.length]}"></span>${escHtml(labelFor(k,BPS_METRIC_LABELS))}</span>`).join(""); }
}

function toggleBpsPanels(rows){
  const has = Array.isArray(rows) && rows.length > 0;
  const metricsCard=$("bpsMetricsCard"); if(metricsCard) metricsCard.classList.toggle("hidden", !has);
  const simCard=$("simulationComparisonCard"); if(simCard) simCard.classList.toggle("hidden", !has);
}

function redrawAll(){
  const d=state.run; drawWeatherVariableChart(); drawCDFChart("cdfChart"); drawXmyStatChart(); drawSimulationChart();
  if(d) drawCarpetPlot("carpetChart", d.monthly_temperature || [], d.monthly_temperature_diurnal_amplitude || [], d.hourly_temperature_by_month || null);
  else drawEmptyChart("carpetChart","No hourly data");
}

let resizeTimer; window.addEventListener("resize",()=>{clearTimeout(resizeTimer); resizeTimer=setTimeout(()=>{ if(state.run) redrawAll(); },150);});

$("buildCommandBtn").addEventListener("click", () => { updateScenarioLabel(); buildCommandPlan(); });
if($("loadSampleBtn")) $("loadSampleBtn").addEventListener("click", loadSampleRun);
$("resetBtn").addEventListener("click", () => location.reload());
["stationSelect","gwlSelect","modeSelect","profileSelect"].forEach(id => $(id).addEventListener("change", updateScenarioLabel));
$("jsonInput").addEventListener("change", async ev => { const file=ev.target.files?.[0]; if(!file)return; try{renderRun(JSON.parse(await file.text()));} catch(err){alert("Could not parse JSON file.\n\n"+err);} });

loadStationCatalog(); updateScenarioLabel(); toggleBpsPanels([]); requestAnimationFrame(()=>{ drawEmptyChart("weatherVariableChart","No data loaded"); drawEmptyChart("cdfChart","No CDF data loaded"); drawEmptyChart("carpetChart","No hourly data loaded"); drawEmptyChart("xmyStatChart","No XMY data loaded"); drawEmptyChart("simulationChart","No BPS results loaded"); });
