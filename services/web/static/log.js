"use strict";

const form = document.getElementById("filters");
const tbody = document.getElementById("rows");
const rowsById = new Map();

function params() {
  const data = new FormData(form);
  const p = new URLSearchParams();
  for (const [k, v] of data.entries()) {
    if (!v) continue;
    if (k === "since" || k === "until") p.set(k, String(new Date(v).getTime() / 1000));
    else p.set(k, v);
  }
  return p;
}

function matches(row) {
  const p = params();
  if (p.get("label") && row.label !== p.get("label")) return false;
  if (p.get("kind") && row.kind !== p.get("kind")) return false;
  if (p.get("min_score") && row.kind === "sighting" && row.max_score < Number(p.get("min_score"))) return false;
  return !p.get("until");   // a fixed end time means "not live"
}

function render(row) {
  const score = row.max_score != null ? Number(row.max_score).toFixed(2) : "";
  return PC.el("tr", {},
    PC.el("td", { class: "num" }, PC.dateTime(row.start)),
    PC.el("td", { class: "num" }, row.kind === "sighting" ? (row.end ? PC.time(row.end) : "now") : ""),
    PC.el("td", {}, PC.el("strong", {}, row.label), row.kind === "event" ? " " : null,
          row.kind === "event" ? PC.el("span", { class: "tag" }, "event") : null),
    PC.el("td", { class: "num" }, score),
    PC.el("td", { class: "num" }, row.max_count || ""),
    PC.el("td", { class: "muted" }, row.detail || row.mode || ""));
}

function upsert(row, prepend) {
  const tr = render(row);
  if (rowsById.has(row.id)) rowsById.get(row.id).replaceWith(tr);
  else if (prepend) tbody.prepend(tr);
  else tbody.append(tr);
  rowsById.set(row.id, tr);
}

async function load() {
  const p = params();
  document.getElementById("csv").href = "/api/log.csv?" + p;
  const rows = await PC.api("GET", "/api/log?" + p);
  tbody.textContent = "";
  rowsById.clear();
  rows.forEach((r) => upsert(r, false));
  document.getElementById("summary").textContent = `${rows.length} entries` + (rows.length >= 500 ? " (newest 500)" : "");
}

async function loadLabels() {
  const select = document.getElementById("label");
  for (const label of await PC.api("GET", "/api/log/labels")) {
    select.append(PC.el("option", { value: label }, label));
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); load(); });
PC.on("log", (row) => {
  if (document.getElementById("live").checked && row.id != null && (rowsById.has(row.id) || matches(row))) {
    upsert(row, true);
  }
});

PC.init("log").then(() => { loadLabels(); load(); });
