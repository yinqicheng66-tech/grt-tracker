"use strict";

// ---------- helpers ----------

const $ = (sel) => document.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";
const pct = (x, digits = 0) => (x * 100).toFixed(digits) + "%";
const fmtInt = (n) => n.toLocaleString("en-CA");

function fmtDelay(s) {
  const sign = s < 0 ? "−" : "+";
  const a = Math.abs(s);
  if (a < 60) return `${sign}${a} s`;
  const m = Math.floor(a / 60);
  const r = a % 60;
  return `${sign}${m} min${r ? " " + r + " s" : ""}`;
}

function el(tag, attrs = {}, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function cssVar(name) {
  return getComputedStyle($(".viz-root")).getPropertyValue(name).trim();
}

// ---------- tooltip ----------

const tip = $("#tooltip");

function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 14;
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  let left = x + pad;
  let top = y + pad;
  if (left + w > window.innerWidth - 8) left = x - w - pad;
  if (top + h > window.innerHeight - 8) top = y - h - pad;
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = Math.max(8, top) + "px";
}
const hideTip = () => { tip.hidden = true; };

function tipRows(title, rows) {
  return `<strong>${title}</strong>` +
    rows.map(([k, v]) => `<div class="row"><span>${k}</span><b>${v}</b></div>`).join("");
}

// ---------- line chart (shared by hourly and daily) ----------

function niceFloor(v, step) { return Math.floor(v / step) * step; }

function lineChart(container, { series, xLabels, tooltip, height = 260 }) {
  container.innerHTML = "";
  const width = Math.max(320, container.clientWidth);
  const m = { top: 12, right: 16, bottom: 28, left: 44 };
  const iw = width - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const n = xLabels.length;

  const values = series.flatMap((s) => s.points.filter((p) => p).map((p) => p.y));
  const yMin = Math.max(0, Math.min(niceFloor(Math.min(...values) - 0.05, 0.1), 0.6));
  const yMax = 1;
  const x = (i) => (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v) => ih - ((v - yMin) / (yMax - yMin)) * ih;

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  const g = el("g", { transform: `translate(${m.left},${m.top})` }, svg);

  for (let t = yMin; t <= yMax + 1e-9; t += 0.1) {
    el("line", { class: "grid", x1: 0, x2: iw, y1: y(t), y2: y(t) }, g);
    const label = el("text", { x: -8, y: y(t) + 4, "text-anchor": "end" }, g);
    label.textContent = pct(t);
  }
  const every = Math.ceil(n / Math.max(2, Math.floor(iw / 56)));
  xLabels.forEach((lab, i) => {
    if (i % every !== 0 && i !== n - 1) return;
    const t = el("text", { x: x(i), y: ih + 20, "text-anchor": "middle" }, g);
    t.textContent = lab;
  });

  for (const s of series) {
    const color = cssVar(s.colorVar);
    let d = "";
    let pen = false;
    s.points.forEach((p, i) => {
      if (!p) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i)},${y(p.y)}`;
      pen = true;
    });
    el("path", { class: "line", d, stroke: color }, g);
    // end marker
    const lastIdx = s.points.map((p) => !!p).lastIndexOf(true);
    if (lastIdx >= 0) el("circle", { class: "dot", cx: x(lastIdx), cy: y(s.points[lastIdx].y), r: 4, fill: color }, g);
  }

  // crosshair + tooltip
  const cross = el("line", { class: "crosshair", y1: 0, y2: ih, visibility: "hidden" }, g);
  const dots = series.map((s) => el("circle", { class: "dot", r: 4, fill: cssVar(s.colorVar), visibility: "hidden" }, g));
  const hit = el("rect", { class: "hit", x: 0, y: 0, width: iw, height: ih }, g);
  const locate = (evt) => {
    const box = hit.getBoundingClientRect();
    const rel = ((evt.clientX - box.left) / box.width) * iw;
    return Math.max(0, Math.min(n - 1, Math.round(n === 1 ? 0 : (rel / iw) * (n - 1))));
  };
  hit.addEventListener("pointermove", (evt) => {
    const i = locate(evt);
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    series.forEach((s, k) => {
      const p = s.points[i];
      dots[k].setAttribute("visibility", p ? "visible" : "hidden");
      if (p) { dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(p.y)); }
    });
    showTip(tooltip(i), evt.clientX, evt.clientY);
  });
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTip();
  });

  container.appendChild(svg);
}

// ---------- horizontal bar chart ----------

function barChart(container, rows) {
  container.innerHTML = "";
  const width = Math.max(320, container.clientWidth);
  const rowH = 26;
  const barH = 16;
  const labelW = Math.min(230, width * 0.42);
  const valueW = 48;
  const m = { top: 4, right: 8, bottom: 24, left: labelW };
  const iw = width - m.left - m.right - valueW;
  const height = m.top + rows.length * rowH + m.bottom;
  const x = (v) => v * iw;

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Routes by on-time rate" });
  const g = el("g", { transform: `translate(${m.left},${m.top})` }, svg);

  for (let t = 0; t <= 1.0001; t += 0.25) {
    el("line", { class: "grid", x1: x(t), x2: x(t), y1: 0, y2: rows.length * rowH }, g);
    const lab = el("text", { x: x(t), y: rows.length * rowH + 16, "text-anchor": "middle" }, g);
    lab.textContent = pct(t);
  }
  el("line", { class: "baseline", x1: 0, x2: 0, y1: 0, y2: rows.length * rowH }, g);

  rows.forEach((r, i) => {
    const cy = i * rowH + rowH / 2;
    const row = el("g", { class: "bar-row", tabindex: 0 }, g);
    el("rect", { class: "row-bg hit", x: -labelW, y: i * rowH, width: labelW + iw + valueW, height: rowH }, row);
    const name = el("text", { x: -10, y: cy + 4, "text-anchor": "end", class: "label-primary" }, row);
    const full = `${r.route} ${r.name}`;
    const maxChars = Math.floor((labelW - 14) / 7);
    name.textContent = full.length > maxChars ? full.slice(0, maxChars - 1) + "…" : full;
    const w = Math.max(2, x(r.on_time));
    const color = cssVar(r.system === "lrt" ? "--series-2" : "--series-1");
    // 4px rounded data end, square at the baseline
    const rad = Math.min(4, w / 2);
    const top = cy - barH / 2;
    el("path", {
      class: "bar",
      fill: color,
      d: `M0,${top}H${w - rad}Q${w},${top} ${w},${top + rad}V${top + barH - rad}Q${w},${top + barH} ${w - rad},${top + barH}H0Z`,
    }, row);
    const val = el("text", { x: w + 6, y: cy + 4, class: "label-secondary" }, row);
    val.textContent = pct(r.on_time);

    const html = tipRows(full, [
      ["On time", pct(r.on_time, 1)],
      ["Late (5+ min)", pct(r.late, 1)],
      ["Median delay", fmtDelay(r.median)],
      ["90th percentile", fmtDelay(r.p90)],
      ["Departures", fmtInt(r.departures)],
    ]);
    row.addEventListener("pointermove", (evt) => showTip(html, evt.clientX, evt.clientY));
    row.addEventListener("pointerleave", hideTip);
    row.addEventListener("focus", () => {
      const b = row.getBoundingClientRect();
      showTip(html, b.left + labelW + w, b.top);
    });
    row.addEventListener("blur", hideTip);
  });

  container.appendChild(svg);
}

// ---------- sections ----------

function renderMeta(data) {
  const opts = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  const first = new Date(data.first_departure).toLocaleString("en-CA", opts);
  const updated = new Date(data.generated_at).toLocaleString("en-CA", opts);
  $("#meta").textContent = `Departures from ${first} onward · updated ${updated} · refreshed hourly`;
}

function renderTiles(data) {
  const bus = data.systems.bus;
  const lrt = data.systems.lrt;
  const total = Object.values(data.systems).reduce((a, s) => a + s.departures, 0);
  const tiles = [];
  if (bus) tiles.push(["Buses on time", pct(bus.on_time, 1), `${fmtInt(bus.departures)} departures`, "--series-1"]);
  if (lrt) tiles.push(["ION light rail on time", pct(lrt.on_time, 1), `${fmtInt(lrt.departures)} departures`, "--series-2"]);
  if (bus) tiles.push(["Median bus delay", fmtDelay(bus.median), `90th percentile ${fmtDelay(bus.p90)}`]);
  tiles.push(["Departures measured", fmtInt(total), "first stops excluded"]);
  $("#tiles").innerHTML = tiles.map(([label, value, note, key]) => `
    <div class="tile">
      <div class="label">${key ? `<span class="key" style="background:var(${key})"></span>` : ""}${label}</div>
      <div class="value">${value}</div>
      <div class="note">${note}</div>
    </div>`).join("");
}

function renderHourly(data) {
  const rows = data.hourly.bus || [];
  if (!rows.length) return;
  const hours = rows.map((r) => r.hour);
  const lo = Math.min(...hours);
  const hi = Math.max(...hours);
  const byHour = Object.fromEntries(rows.map((r) => [r.hour, r]));
  const span = [];
  for (let h = lo; h <= hi; h++) span.push(h);
  const label = (h) => (h % 12 || 12) + (h < 12 ? " am" : " pm");
  lineChart($("#hourly"), {
    xLabels: span.map(label),
    series: [{ colorVar: "--series-1", points: span.map((h) => byHour[h] ? { y: byHour[h].on_time } : null) }],
    tooltip: (i) => {
      const r = byHour[span[i]];
      if (!r) return tipRows(label(span[i]), [["No data", ""]]);
      return tipRows(`${label(span[i])} – ${label((span[i] + 1) % 24)}`, [
        ["On time", pct(r.on_time, 1)],
        ["Median delay", fmtDelay(r.median)],
        ["Departures", fmtInt(r.departures)],
      ]);
    },
  });
}

function renderDaily(data) {
  const systems = Object.keys(data.daily).filter((s) => data.daily[s].length);
  const dates = [...new Set(systems.flatMap((s) => data.daily[s].map((d) => d.date)))].sort();
  if (dates.length < 2) return;
  $("#daily-card").hidden = false;
  const names = { bus: "Buses", lrt: "ION light rail" };
  const colors = { bus: "--series-1", lrt: "--series-2" };
  $("#daily-legend").innerHTML = systems.map((s) =>
    `<span><span class="key" style="background:var(${colors[s]})"></span>${names[s] || s}</span>`).join("");
  const lookup = Object.fromEntries(systems.map((s) => [s, Object.fromEntries(data.daily[s].map((d) => [d.date, d]))]));
  const label = (d) => new Date(d + "T12:00").toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" });
  lineChart($("#daily"), {
    xLabels: dates.map(label),
    series: systems.map((s) => ({ colorVar: colors[s], points: dates.map((d) => lookup[s][d] ? { y: lookup[s][d].on_time } : null) })),
    tooltip: (i) => tipRows(label(dates[i]), systems.map((s) => {
      const r = lookup[s][dates[i]];
      return [names[s] || s, r ? `${pct(r.on_time, 1)} of ${fmtInt(r.departures)}` : "no data"];
    })),
  });
}

let routeFilter = "bus";

function renderRoutes(data) {
  const rows = data.routes.filter((r) => routeFilter === "all" || r.system === routeFilter);
  barChart($("#routes"), rows);
  $("#routes-table").innerHTML =
    "<thead><tr><th>Route</th><th>Name</th><th>On time</th><th>Late</th><th>Median</th><th>90th %</th><th>Departures</th></tr></thead><tbody>" +
    rows.map((r) => `<tr><td>${r.route}</td><td>${r.name}</td><td>${pct(r.on_time, 1)}</td><td>${pct(r.late, 1)}</td>` +
      `<td>${fmtDelay(r.median)}</td><td>${fmtDelay(r.p90)}</td><td>${fmtInt(r.departures)}</td></tr>`).join("") +
    "</tbody>";
}

// Sequential blue ramp, light to dark: longer delay = darker.
const DELAY_BINS = [
  { max: 60, color: "#86b6ef", label: "< 1 min" },
  { max: 120, color: "#5598e7", label: "1–2" },
  { max: 180, color: "#2a78d6", label: "2–3" },
  { max: 300, color: "#1c5cab", label: "3–5" },
  { max: Infinity, color: "#104281", label: "5+ min" },
];
const binFor = (s) => DELAY_BINS.find((b) => s < b.max);

function renderMap(data) {
  $("#map-scale").innerHTML = `<span>Median delay</span><span class="ramp">${DELAY_BINS.map((b) =>
    `<span style="background:${b.color}" title="${b.label}"></span>`).join("")}</span><span>${DELAY_BINS[0].label} … ${DELAY_BINS[DELAY_BINS.length - 1].label}</span>`;
  if (typeof L === "undefined") {
    $("#map").textContent = "Map unavailable (could not load the map library).";
    return;
  }
  const map = L.map("map", { scrollWheelZoom: false }).setView([43.45, -80.49], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  const stops = [...data.stops].sort((a, b) => a.median - b.median); // worst drawn last, on top
  const bounds = [];
  for (const s of stops) {
    const bin = binFor(s.median);
    const marker = L.circleMarker([s.lat, s.lon], {
      radius: 4 + Math.min(8, Math.max(0, s.median) / 60),
      color: cssVar("--surface-1") || "#fff",
      weight: 2,
      fillColor: bin.color,
      fillOpacity: 0.9,
    }).addTo(map);
    marker.bindTooltip(tipRows(s.name, [
      ["Median delay", fmtDelay(s.median)],
      ["On time", pct(s.on_time, 1)],
      ["Departures", fmtInt(s.departures)],
    ]), { className: "map-tip", direction: "top", offset: [0, -6] });
    bounds.push([s.lat, s.lon]);
  }
  if (bounds.length) map.fitBounds(bounds, { padding: [20, 20] });
}

// ---------- boot ----------

async function main() {
  let data;
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    data = await res.json();
  } catch (err) {
    $("#meta").textContent = "Could not load data.json.";
    return;
  }
  renderMeta(data);
  renderTiles(data);
  const charts = () => { renderHourly(data); renderDaily(data); renderRoutes(data); };
  charts();
  renderMap(data);

  document.querySelectorAll(".segmented button").forEach((btn) => {
    btn.addEventListener("click", () => {
      routeFilter = btn.dataset.system;
      document.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      renderRoutes(data);
    });
  });

  let resizeTimer;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(charts, 150);
  });
}

main();
