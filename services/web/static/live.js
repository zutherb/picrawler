"use strict";

const video = document.getElementById("video");
const canvas = document.getElementById("overlay");
const ctx = canvas.getContext("2d");
const showBoxes = document.getElementById("boxes");
const feed = document.getElementById("feed");
const FEED_MAX = 50;
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

const COLORS = ["#ffd400", "#00e5ff", "#ff6bd6", "#7cff6b", "#ff9f40"];
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
});

document.getElementById("stop-sit").addEventListener("click", () => PC.stop(true));

PC.init("live").then(() => { startVideo(); requestAnimationFrame(draw); });
