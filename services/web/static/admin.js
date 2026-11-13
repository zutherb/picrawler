"use strict";

// --- bluetooth ---

const devicesBody = document.getElementById("devices");
const scanButton = document.getElementById("scan");
const busy = new Set();

function deviceState(d) {
  if (busy.has(d.mac)) return "working…";
  if (d.connected) return "connected";
  if (d.paired) return "paired";
  return "found";
}

function renderDevices(devices) {
  devicesBody.textContent = "";
  if (!devices.length) {
    devicesBody.append(PC.el("tr", {}, PC.el("td", { colspan: "5", class: "muted" }, "No devices yet. Search first.")));
    return;
  }
  for (const d of devices) {
    const actions = PC.el("td", { class: "row" });
    const add = (label, action, cls) => actions.append(
      PC.el("button", { class: cls || "", onclick: () => btAction(action, d), ...(busy.has(d.mac) ? { disabled: "" } : {}) }, label));
    if (!d.paired) add("Pair", "pair", "primary");
    else if (!d.connected) add("Connect", "connect", "primary");
    else add("Disconnect", "disconnect");
    if (d.paired) add("Remove", "remove", "danger");
    devicesBody.append(PC.el("tr", {},
      PC.el("td", {}, PC.el("strong", {}, d.name), d.gamepad ? " " : null,
            d.gamepad ? PC.el("span", { class: "tag" }, "gamepad") : null),
      PC.el("td", { class: "num muted" }, d.mac),
      PC.el("td", {}, PC.el("span", { class: "dot " + (d.connected ? "ok" : d.paired ? "warn" : "") }), deviceState(d)),
      PC.el("td", { class: "num" }, d.battery != null ? `${d.battery} %` : ""),
      actions));
  }
}

let lastDevices = [];
async function loadDevices() {
  try {
    const data = await PC.api("GET", "/api/bluetooth");
    lastDevices = data.devices;
    scanButton.disabled = data.scanning;
    renderDevices(lastDevices);
  } catch (err) {
    document.getElementById("bt-note").textContent = "Bluetooth: " + err.message;
  }
}

async function btAction(action, d) {
  if (action === "remove" && !confirm(`Remove ${d.name}? It has to be paired again.`)) return;
  busy.add(d.mac);
  renderDevices(lastDevices);
  try {
    const result = await PC.api("POST", `/api/bluetooth/${action}`, { mac: d.mac });
    if (!result.pending) { busy.delete(d.mac); await loadDevices(); }
    else PC.toast(action === "pair" ? "Pairing… keep the controller in pairing mode" : "Connecting…");
  } catch (err) {
    busy.delete(d.mac);
    PC.toast(`${action} failed: ${err.message}`, true);
    renderDevices(lastDevices);
  }
}

scanButton.addEventListener("click", async () => {
  try {
    await PC.api("POST", "/api/bluetooth/scan", { seconds: 20 });
    scanButton.disabled = true;
    document.getElementById("bt-note").textContent = "Searching for 20 s…";
  } catch (err) {
    PC.toast("Search failed: " + err.message, true);
  }
});

PC.on("bluetooth", (msg) => {
  if (msg.mac) busy.delete(msg.mac);
  if (msg.action === "scan") {
    scanButton.disabled = false;
    document.getElementById("bt-note").textContent = "Search finished.";
  } else if (msg.result && !msg.result.ok) {
    PC.toast(`${msg.action} failed: ${msg.result.error || "unknown error"}`, true);
  } else {
    PC.toast(`${msg.action}: done`);
  }
  if (msg.devices) { lastDevices = msg.devices; renderDevices(lastDevices); } else loadDevices();
});

// --- gamepad input ---

PC.on("gamepad", (s) => {
  document.getElementById("pad-xy").textContent = `${Number(s.x).toFixed(2)} / ${Number(s.y).toFixed(2)}`;
  const pressed = s.buttons || [];
  document.getElementById("pad-buttons").textContent = pressed.length ? pressed.join(", ") : "–";
  if (pressed.length) document.getElementById("pad-last").textContent = pressed[pressed.length - 1];
});

// --- vision mode ---

for (const button of document.querySelectorAll("#modes button")) {
  button.addEventListener("click", async () => {
    try {
      await PC.api("POST", "/api/vision/mode", { mode: button.dataset.mode });
    } catch (err) {
      PC.toast("Mode change failed: " + err.message, true);
    }
  });
}

PC.on("status", (s) => {
  const v = s.vision || {};
  for (const b of document.querySelectorAll("#modes button")) b.classList.toggle("selected", b.dataset.mode === v.mode);
  const note = v.status !== "online" ? "vision service offline" : (v.error || (v.tpu ? "TPU ready" : "no TPU"));
  document.getElementById("vision-note").textContent = note;
});

// --- services ---

const journal = document.getElementById("journal");

const MB = 2 ** 20, GB = 2 ** 30;
const size = (bytes) => bytes == null ? "–" : bytes >= GB ? `${(bytes / GB).toFixed(1)} GB` : `${Math.round(bytes / MB)} MB`;
const level = (pct, warn, bad) => pct == null ? "" : pct >= bad ? "bad" : pct >= warn ? "warn" : "ok";

async function loadSystem(withServices = true) {
  let data;
  try {
    data = await PC.api("GET", "/api/system");
  } catch (err) {
    return;
  }
  if (withServices) renderServices(data.services);
  renderInfo(data.info);
}

function renderServices(services) {
  const body = document.getElementById("services");
  body.textContent = "";
  for (const s of services) {
    const state = s.active === "active" ? "ok" : s.active === "failed" ? "bad" : "warn";
    body.append(PC.el("tr", {},
      PC.el("td", {}, s.unit),
      PC.el("td", {}, PC.el("span", { class: "dot " + state }), `${s.active} (${s.sub})`),
      PC.el("td", { class: "num" }, size(s.memory)),
      PC.el("td", { class: "muted" }, s.since),
      PC.el("td", { class: "row" },
        PC.el("button", { onclick: () => showJournal(s.unit) }, "Log"),
        PC.el("button", { onclick: () => restart(s.unit) }, "Restart"))));
  }
}

function renderInfo(info) {
  const sys = document.getElementById("system");
  sys.textContent = "";
  const t = info.throttled;
  const tile = (label, value, level) => PC.el("div", { class: "tile" }, PC.el("div", { class: "label" }, label),
    PC.el("div", { class: "value" }, PC.el("span", { class: "dot " + (level || "") }), value));
  sys.append(tile("CPU", info.cpu_percent != null ? `${info.cpu_percent} %` : "–", level(info.cpu_percent, 70, 90)));
  if (info.load) sys.append(tile("Load (1/5/15 min)", info.load.map((v) => v.toFixed(2)).join(" / "), level(info.load[0], 3, 4)));
  const mem = info.memory, disk = info.disk;
  sys.append(tile("Memory", mem ? `${size(mem.used)} / ${size(mem.total)} (${mem.percent} %)` : "–", level(mem && mem.percent, 75, 90)));
  sys.append(tile("Disk", disk ? `${size(disk.used)} / ${size(disk.total)} (${disk.percent} %)` : "–", level(disk && disk.percent, 80, 95)));
  sys.append(tile("CPU temperature", info.cpu_temp_c != null ? `${info.cpu_temp_c} °C` : "–",
                  info.cpu_temp_c > 80 ? "bad" : info.cpu_temp_c > 70 ? "warn" : "ok"));
  sys.append(tile("Power", !t ? "–" : t.under_voltage ? "under-voltage now" : t.under_voltage_since_boot
                  ? "under-voltage since boot" : "ok", !t ? "" : t.under_voltage ? "bad" : t.under_voltage_since_boot ? "warn" : "ok"));
  sys.append(tile("Throttling", !t ? "–" : t.throttled ? "throttled now" : t.throttled_since_boot ? "since boot" : "none",
                  !t ? "" : t.throttled ? "bad" : t.throttled_since_boot ? "warn" : "ok"));
  const net = info.network;
  if (net) {
    const text = net.mode === "hotspot" ? `hotspot ${net.ip} · ${net.clients} connected`
      : net.mode === "client" ? `Wi-Fi ${net.connection}` : net.mode;
    sys.append(tile("Network", text, net.mode === "client" ? "ok" : net.mode === "hotspot" ? "warn" : "bad"));
  }
  if (info.uptime_s != null) sys.append(tile("Uptime", `${Math.floor(info.uptime_s / 3600)} h ${Math.floor(info.uptime_s / 60) % 60} min`, "ok"));
}

const HW_ORDER = { bad: 0, warn: 1, unknown: 2, ok: 3 };

async function loadHardware(fresh = false) {
  let data;
  try {
    data = await PC.api("GET", "/api/system/hardware" + (fresh ? "?fresh=1" : ""));
  } catch (err) {
    return;
  }
  const box = document.getElementById("hardware");
  box.textContent = "";
  for (const c of data.components) {
    box.append(PC.el("div", { class: "tile" }, PC.el("div", { class: "label" }, c.name),
      PC.el("div", { class: "value" }, PC.el("span", { class: "dot " + (c.status === "unknown" ? "" : c.status) }),
        c.status === "ok" ? "ok" : c.status === "unknown" ? "unknown" : c.status === "warn" ? "check" : "problem"),
      PC.el("div", { class: "detail" }, c.detail)));
  }
  const worst = data.components.reduce((w, c) => HW_ORDER[c.status] < HW_ORDER[w] ? c.status : w, "ok");
  const bad = data.components.filter((c) => c.status === "bad" || c.status === "warn").length;
  document.getElementById("hw-summary").textContent = bad ? `${bad} need attention` : `all ${data.components.length} ok`;
  document.getElementById("hw-summary").className = "muted hw-" + worst;
}

async function showJournal(unit) {
  document.getElementById("journal-card").classList.remove("hidden");
  document.getElementById("journal-title").textContent = `journalctl -u ${unit} -n 100`;
  journal.textContent = "Loading…";
  try {
    journal.textContent = await PC.api("GET", "/api/system/journal?unit=" + encodeURIComponent(unit));
    journal.scrollTop = journal.scrollHeight;
  } catch (err) {
    journal.textContent = err.message;
  }
}

async function restart(unit) {
  if (!confirm(`Restart ${unit}?`)) return;
  try {
    await PC.api("POST", "/api/system/restart", { unit });
    PC.toast(`${unit} restarted`);
    loadSystem();
  } catch (err) {
    PC.toast("Restart failed: " + err.message, true);
  }
}

async function power(action) {
  const text = action === "poweroff" ? "Shut down the robot?" : "Reboot the robot?";
  if (!confirm(text)) return;
  try {
    PC.toast("Sitting down, then " + (action === "poweroff" ? "shutting down…" : "rebooting…"));
    await PC.api("POST", "/api/system/power", { action });
  } catch (err) {
    PC.toast(`${action} failed: ${err.message}`, true);
  }
}

document.getElementById("refresh").addEventListener("click", () => loadSystem(true));
document.getElementById("hw-check").addEventListener("click", () => loadHardware(true));
document.getElementById("reboot").addEventListener("click", () => power("reboot"));
document.getElementById("poweroff").addEventListener("click", () => power("poweroff"));

PC.init("admin").then(() => {
  loadDevices();
  loadSystem(true);
  loadHardware();
  let ticks = 0;
  setInterval(() => {
    ticks += 1;
    loadSystem(ticks % 5 === 0);
    if (ticks % 5 === 0) loadHardware();
  }, 2000);
});
