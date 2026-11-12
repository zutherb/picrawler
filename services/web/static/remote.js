"use strict";

// The page only reports where the stick is; the arbiter turns that into
// steps. While a control is held we send every SEND_MS; on release one
// centered message. If we stop sending, the arbiter centers by itself.
const SEND_MS = 150;
const DEADZONE = 0.2;
const PAD_ACTIONS = { 0: "stand", 1: "sit", 3: "wave", 2: "dance" };   // standard gamepad mapping

const thumb = document.getElementById("thumb");
const joystick = document.getElementById("joystick");
const speedInput = document.getElementById("speed");
const keys = new Set();
let pointer = null;              // {x, y} while dragging
let sentCentered = true;
let padButtons = [];

// --- inputs ---

function keyStick() {
  let x = 0, y = 0;
  if (keys.has("ArrowUp") || keys.has("w")) y -= 1;
  if (keys.has("ArrowDown") || keys.has("s")) y += 1;
  if (keys.has("ArrowLeft") || keys.has("a")) x -= 1;
  if (keys.has("ArrowRight") || keys.has("d")) x += 1;
  return { x, y };
}

function padStick() {
  const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
  if (!pads.length) return null;
  const pad = pads[0];
  const dz = (v) => (Math.abs(v) < DEADZONE ? 0 : v);
  // one-shot actions on button press
  pad.buttons.forEach((b, i) => {
    if (b.pressed && !padButtons[i] && PAD_ACTIONS[i]) sendAction(PAD_ACTIONS[i]);
    padButtons[i] = b.pressed;
  });
  return { x: dz(pad.axes[0] || 0), y: dz(pad.axes[1] || 0), name: pad.id };
}

function currentStick() {
  if (pointer) return { ...pointer, source: "touch" };
  const k = keyStick();
  if (k.x || k.y) return { ...k, source: "keyboard" };
  const p = padStick();
  if (p && (p.x || p.y)) return { x: p.x, y: p.y, source: "gamepad" };
  return { x: 0, y: 0, source: p ? `gamepad: ${p.name}` : null };
}

// --- joystick widget ---

function pointerToStick(e) {
  const r = joystick.getBoundingClientRect();
  const radius = r.width / 2;
  let x = (e.clientX - r.left - radius) / radius;
  let y = (e.clientY - r.top - radius) / radius;
  const len = Math.hypot(x, y);
  if (len > 1) { x /= len; y /= len; }
  return { x, y };
}

joystick.addEventListener("pointerdown", (e) => {
  joystick.setPointerCapture(e.pointerId);
  pointer = pointerToStick(e);
});
joystick.addEventListener("pointermove", (e) => { if (pointer) pointer = pointerToStick(e); });
for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
  joystick.addEventListener(type, () => { pointer = null; });
}

function drawThumb(stick) {
  const r = joystick.clientWidth / 2 - thumb.clientWidth / 2;
  thumb.style.transform = `translate(${stick.x * r}px, ${stick.y * r}px)`;
  joystick.classList.toggle("active", Boolean(stick.x || stick.y));
}

// --- keyboard ---

const DRIVE_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "w", "a", "s", "d"]);
document.addEventListener("keydown", (e) => {
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (!DRIVE_KEYS.has(key) || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  e.preventDefault();
  keys.add(key);
});
document.addEventListener("keyup", (e) => keys.delete(e.key.length === 1 ? e.key.toLowerCase() : e.key));
window.addEventListener("blur", () => { keys.clear(); pointer = null; });

// --- sending ---

function speed() { return Number(speedInput.value); }

async function send(body) {
  try {
    await PC.api("POST", "/api/remote", body);
  } catch (err) {
    PC.toast("Remote: " + err.message, true);
  }
}

function sendAction(action) {
  send({ x: 0, y: 0, action, speed: speed() });
}

let lastSend = 0;
function loop(now) {
  const stick = currentStick();
  drawThumb(stick);
  document.getElementById("source").textContent = stick.source || "drag, WASD / arrows, or a gamepad";
  const moving = Math.hypot(stick.x, stick.y) > DEADZONE;
  if (moving && now - lastSend >= SEND_MS) {
    lastSend = now;
    sentCentered = false;
    send({ x: +stick.x.toFixed(3), y: +stick.y.toFixed(3), speed: speed() });
  } else if (!moving && !sentCentered) {
    sentCentered = true;
    send({ x: 0, y: 0, speed: speed() });
  }
  requestAnimationFrame(loop);
}

speedInput.addEventListener("input", () => { document.getElementById("speed-value").textContent = speedInput.value; });
for (const b of document.querySelectorAll("#actions button")) {
  b.addEventListener("click", () => sendAction(b.dataset.action));
}
document.getElementById("big-stop").addEventListener("click", () => PC.stop(false));

// --- video and status ---

const video = document.getElementById("video");
function startVideo() { video.src = "/stream.mjpg?" + Date.now(); }
video.addEventListener("load", () => document.getElementById("placeholder").classList.add("hidden"));
video.addEventListener("error", () => {
  document.getElementById("placeholder").classList.remove("hidden");
  setTimeout(startVideo, 3000);
});

function chip(text, level) {
  return PC.el("span", { class: "chip" }, PC.el("span", { class: "dot " + (level || "") }), text);
}

PC.on("status", (s) => {
  const chips = document.getElementById("chips");
  chips.textContent = "";
  const m = s.motion || {};
  const running = m.status === "running";
  document.getElementById("mode").textContent = running ? `running ${m.name || ""}` : (m.status || "unknown");
  chips.append(chip(s.body && s.body.status === "online" ? "body online" : "body offline",
                    s.body && s.body.status === "online" ? "ok" : "bad"));
  if (s.distance_cm != null) chips.append(chip(`${s.distance_cm} cm ahead`, s.distance_cm < 15 ? "bad" : s.distance_cm < 25 ? "warn" : "ok"));
  const b = s.battery;
  if (b && b.present) chips.append(chip(`battery ${b.percent} %`, b.level === "critical" ? "bad" : b.level === "low" ? "warn" : "ok"));
  if (m.status === "error") chips.append(chip(m.error || "error", "bad"));
});

PC.init("remote").then(() => {
  if (PC.me.role !== "admin") { location.href = "/"; return; }
  startVideo();
  requestAnimationFrame(loop);
});

// --- surroundings (map/local from the arbiter's scan) ---

const mapCanvas = document.getElementById("map");
let lastMap = null;

function drawMap(m) {
  const ctx = mapCanvas.getContext("2d");
  const size = mapCanvas.width;
  const css = getComputedStyle(document.documentElement);
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = css.getPropertyValue("--bg-deep-2");
  ctx.fillRect(0, 0, size, size);
  const radius = (m && m.radius_cm) || 150;
  const scale = size / (2 * radius);              // px per cm
  const center = size / 2;
  if (m && m.grid && m.grid.length) {
    const n = m.grid.length;
    const cell = size / n;
    m.grid.forEach((row, r) => {
      for (let c = 0; c < row.length; c++) {
        if (row[c] === ".") ctx.fillStyle = "rgba(48, 213, 192, 0.28)";
        else if (row[c] === "#") ctx.fillStyle = css.getPropertyValue("--warn");
        else continue;
        ctx.fillRect(c * cell, r * cell, Math.ceil(cell), Math.ceil(cell));
      }
    });
  }
  // range rings
  ctx.strokeStyle = "rgba(244, 240, 255, 0.12)";
  ctx.fillStyle = "rgba(244, 240, 255, 0.45)";
  ctx.font = "11px " + css.getPropertyValue("--font-mono");
  for (let cm = 50; cm <= radius; cm += 50) {
    ctx.beginPath();
    ctx.arc(center, center, cm * scale, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.fillText(cm + " cm", center + 4, center - cm * scale + 12);
  }
  // measurement rays: heading 0 is up, positive turns left (counter-clockwise)
  for (const s of (m && m.samples) || []) {
    const a = s.heading_deg * Math.PI / 180;
    const d = Math.min(s.cm == null ? radius : s.cm, radius) * scale;
    ctx.strokeStyle = s.cm == null ? "rgba(244, 240, 255, 0.15)" : "rgba(160, 243, 227, 0.55)";
    ctx.beginPath();
    ctx.moveTo(center, center);
    ctx.lineTo(center - Math.sin(a) * d, center - Math.cos(a) * d);
    ctx.stroke();
  }
  // the crawler, facing where it faced when the scan started
  ctx.fillStyle = css.getPropertyValue("--accent-1");
  ctx.beginPath();
  ctx.moveTo(center, center - 11);
  ctx.lineTo(center - 8, center + 8);
  ctx.lineTo(center + 8, center + 8);
  ctx.closePath();
  ctx.fill();
}

function showMap(m) {
  lastMap = m;
  drawMap(m);
  const status = document.getElementById("map-status");
  const info = document.getElementById("map-info");
  const button = document.getElementById("map-scan");
  const [done, total] = m.progress || [0, 0];
  const scanning = m.status === "scanning";
  status.textContent = scanning ? `scanning ${done}/${total}` : m.status;
  status.classList.toggle("warn", m.status === "aborted" || m.status === "refused");
  button.disabled = scanning;
  if (m.message) {
    info.textContent = m.message;
  } else if (m.status === "done") {
    const parts = [new Date(m.ts * 1000).toLocaleTimeString()];
    if (m.nearest) parts.push(`nearest ${Math.round(m.nearest[1])} cm at ${Math.round(m.nearest[0])}°`);
    if (m.clearest_deg != null) parts.push(`most room at ${Math.round(m.clearest_deg)}°`);
    info.textContent = parts.join(" · ") + ". Only valid where it was scanned.";
  } else if (scanning) {
    info.textContent = "Turning and measuring…";
  }
}

PC.on("map", showMap);
document.getElementById("map-scan").addEventListener("click", async () => {
  try {
    await PC.api("POST", "/api/map/scan", {});
  } catch (err) {
    PC.toast("Scan: " + err.message, true);
  }
});
drawMap(null);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => drawMap(lastMap));
