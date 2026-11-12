"use strict";

const video = document.getElementById("video");
const canvas = document.getElementById("overlay");
const ctx = canvas.getContext("2d");
const showBoxes = document.getElementById("boxes");
const feed = document.getElementById("feed");
const FEED_MAX = 6;          // compact list; the dialog shows everything
const BOX_TTL_MS = 600;
let lastDetections = { objects: [], at: 0 };
const feedRows = new Map();

// --- video ---

function startVideo() {
  video.src = "/stream.mjpg?" + Date.now();
}
video.addEventListener("load", () => document.getElementById("placeholder").classList.add("hidden"));
video.addEventListener("error", () => {
  document.getElementById("placeholder").classList.remove("hidden");
  setTimeout(startVideo, 3000);
});

// --- boxes ---

const COLORS = ["#30D5C0", "#A0F3E3", "#FEBC2E", "#FF5F57", "#B8ADD9"];
function color(label) {
  let h = 0;
  for (const c of label) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
}

function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.clearRect(0, 0, w, h);
  const fresh = performance.now() - lastDetections.at < BOX_TTL_MS;
  if (showBoxes.checked && fresh) {
    ctx.lineWidth = 2;
    ctx.font = "13px system-ui, sans-serif";
    for (const o of lastDetections.objects) {
      const [x0, y0, x1, y1] = o.box;
      const c = color(o.label);
      ctx.strokeStyle = c;
      ctx.strokeRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
      const text = `${o.label} ${o.score.toFixed(2)}`;
      const tw = ctx.measureText(text).width + 8;
      ctx.fillStyle = c;
      ctx.fillRect(x0 * w, Math.max(0, y0 * h - 18), tw, 18);
      ctx.fillStyle = "#000";
      ctx.fillText(text, x0 * w + 4, Math.max(13, y0 * h - 5));
    }
  }
  requestAnimationFrame(draw);
}

PC.on("detections", (d) => {
  lastDetections = { objects: d.objects || [], at: performance.now() };
  const labels = (d.objects || []).map((o) => o.label);
  document.getElementById("objects").textContent = labels.length ? labels.join(", ") : "";
});

// --- OLED mirror ---

const oled = document.getElementById("oled");
function showOled(frame) {
  PC.drawOled(oled, frame);
  document.getElementById("oled-note").textContent = "mirror, " + PC.time(frame.ts);
}
PC.on("display", showOled);

async function loadOled() {
  try {
    showOled(await PC.api("GET", "/api/display"));
  } catch (err) {
    document.getElementById("oled-note").textContent = "no picture (display service offline or without MQTT)";
  }
}

// --- recent log ---

function feedItem(row) {
  const { title, text } = PC.describe(row);
  return PC.el("li", {}, PC.el("strong", {}, title), " ",
               PC.el("span", { class: "muted" }, text), " ",
               row.kind === "event" ? PC.el("span", { class: "tag" }, "event") : null);
}

function addFeed(row) {
  const key = row.id != null ? row.id : `${row.label}-${row.start}`;
  const item = feedItem(row);
  if (feedRows.has(key)) {
    feedRows.get(key).replaceWith(item);
  } else {
    feed.prepend(item);
    while (feed.children.length > FEED_MAX) feed.lastChild.remove();
  }
  feedRows.set(key, item);
}
PC.on("history", (rows) => { feed.textContent = ""; feedRows.clear(); rows.forEach(addFeed); });
PC.on("log", addFeed);

// --- dialog with all entries ---

const dialog = document.getElementById("log-dialog");
const dialogRows = document.getElementById("log-rows");
const dialogFilter = document.getElementById("log-filter");
const dialogById = new Map();

function logRow(row) {
  const score = row.max_score != null ? Number(row.max_score).toFixed(2) : "";
  const tr = PC.el("tr", {},
    PC.el("td", { class: "num" }, PC.dateTime(row.start)),
    PC.el("td", { class: "num" }, row.kind === "sighting" ? (row.end ? PC.time(row.end) : "now") : ""),
    PC.el("td", {}, PC.el("strong", {}, row.label), row.kind === "event" ? " " : null,
          row.kind === "event" ? PC.el("span", { class: "tag" }, "event") : null),
    PC.el("td", { class: "num" }, score),
    PC.el("td", { class: "num" }, row.max_count || ""),
    PC.el("td", { class: "muted" }, row.detail || row.mode || ""));
  tr.dataset.search = `${row.label} ${row.detail || ""} ${row.mode || ""}`.toLowerCase();
  return tr;
}

function applyFilter() {
  const q = dialogFilter.value.trim().toLowerCase();
  let shown = 0;
  for (const tr of dialogRows.children) {
    const match = !q || tr.dataset.search.includes(q);
    tr.classList.toggle("hidden", !match);
    shown += match ? 1 : 0;
  }
  document.getElementById("log-count").textContent = `${shown} entries`;
}

function upsertDialog(row, prepend) {
  if (row.id == null) return;
  const tr = logRow(row);
  if (dialogById.has(row.id)) dialogById.get(row.id).replaceWith(tr);
  else if (prepend) dialogRows.prepend(tr);
  else dialogRows.append(tr);
  dialogById.set(row.id, tr);
}

async function openDialog() {
  dialogRows.textContent = "";
  dialogById.clear();
  dialog.showModal();
  try {
    (await PC.api("GET", "/api/log?limit=500")).forEach((r) => upsertDialog(r, false));
  } catch (err) {
    PC.toast("Log: " + err.message, true);
  }
  applyFilter();
}

const recent = document.getElementById("recent");
recent.addEventListener("click", openDialog);
recent.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDialog(); } });
document.getElementById("log-close").addEventListener("click", () => dialog.close());
document.getElementById("dialog-stop").addEventListener("click", () => PC.stop(false));
dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });   // click on the backdrop
dialogFilter.addEventListener("input", applyFilter);
PC.on("log", (row) => {
  if (!dialog.open) return;
  upsertDialog(row, true);
  applyFilter();
});

// --- status tiles ---

function tile(label, value, level) {
  return PC.el("div", { class: "tile" }, PC.el("div", { class: "label" }, label),
               PC.el("div", { class: "value" }, PC.el("span", { class: "dot " + (level || "") }), value));
}

function online(x) { return x && x.status === "online"; }

PC.on("status", (s) => {
  const tiles = document.getElementById("tiles");
  tiles.textContent = "";
  tiles.append(tile("Body", online(s.body) ? "online" : "offline", online(s.body) ? "ok" : "bad"));
  const b = s.battery;
  if (b && b.present) {
    tiles.append(tile("Battery", `${b.percent} % · ${b.voltage.toFixed(2)} V`,
                      b.level === "critical" ? "bad" : b.level === "low" ? "warn" : "ok"));
  }
  const m = s.motion || {};
  const motion = m.status === "running" ? `running ${m.name || ""}` : (m.status || "unknown");
  tiles.append(tile("Motion", motion, m.status === "error" ? "bad" : m.status === "running" ? "warn" : "ok"));
  tiles.append(tile("Arbiter", online(s.arbiter) ? "online" : "offline", online(s.arbiter) ? "ok" : "bad"));
  const v = s.vision || {};
  const vtext = online(v) ? `${v.mode}${v.tpu ? "" : ", no TPU"} · ${v.fps ?? "?"} fps` : "offline";
  tiles.append(tile("Vision", vtext, !online(v) ? "bad" : (v.error || !v.tpu) ? "warn" : "ok"));
  if (online(v) && v.inference_ms != null) tiles.append(tile("Inference", `${v.inference_ms} ms`, "ok"));
  tiles.append(tile("Distance", s.distance_cm != null ? `${s.distance_cm} cm` : "–",
                    s.distance_cm != null && s.distance_cm < 15 ? "bad" : s.distance_cm != null ? "ok" : ""));
  const pad = (s.sensors || {}).gamepad;
  const padText = pad && pad.connected ? (pad.name || "connected") : "not connected";
  tiles.append(tile("Gamepad", padText, pad && pad.connected ? "ok" : ""));
  if (v.error) tiles.append(tile("Vision note", v.error, "warn"));
  const speech = (s.sensors || {}).speech;
  if (speech) tiles.append(tile("Microphone", online(speech) ? (speech.error ? "ASR error" : "listening") : "offline",
                                !online(speech) ? "bad" : speech.error ? "warn" : "ok"));
  const a = s.agent;
  if (a) {
    const text = online(a) ? (a.busy ? "thinking…" : "ready") : "offline";
    tiles.append(tile("Agent", text, !online(a) ? "bad" : a.busy ? "warn" : "ok"));
    document.getElementById("agent-state").textContent = online(a) ? `${text} · ${(a.brains || []).join(" → ")}` : "agent offline";
  }
});

document.getElementById("stop-sit").addEventListener("click", () => PC.stop(true));

// --- agent ---

const agentReplies = document.getElementById("agent-replies");
PC.on("agent", (r) => {
  const meta = [r.source, r.brain, r.seconds != null ? `${r.seconds} s` : null].filter(Boolean).join(" · ");
  agentReplies.prepend(PC.el("li", {}, PC.el("span", { class: "time" }, PC.time(r.ts)),
    PC.el("span", { class: "muted" }, `${r.request} → `), PC.el("strong", {}, r.text), " ",
    PC.el("span", { class: "tag" }, meta)));
  while (agentReplies.children.length > 20) agentReplies.lastChild.remove();
});
document.getElementById("agent-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("agent-text");
  if (!input.value.trim()) return;
  try {
    await PC.api("POST", "/api/agent/ask", { text: input.value });
    input.value = "";
  } catch (err) {
    PC.toast("Agent: " + err.message, true);
  }
});

PC.init("live").then(() => {
  if (PC.me.role === "admin") document.getElementById("agent-panel").classList.remove("hidden");
  startVideo();
  loadOled();
  requestAnimationFrame(draw);
});
