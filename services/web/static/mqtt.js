"use strict";

// --- state ---

const root = { name: "", path: "", children: new Map(), count: 0, el: null };
let selected = "";              // "" = all topics
let paused = false;
const pending = [];             // messages waiting for the next render frame
const MAX_SHOWN = 300;
const NOISY = new Set(["cmd/heartbeat"]);
let received = 0;

const treeEl = document.getElementById("tree");
const list = document.getElementById("messages");
const filterInput = document.getElementById("filter");
const exactBox = document.getElementById("exact");
const hideNoisy = document.getElementById("hide-noisy");

// --- topic tree ---

function nodeFor(path) {
  let node = root;
  let current = "";
  for (const part of path.split("/")) {
    current = current ? `${current}/${part}` : part;
    if (!node.children.has(part)) {
      node.children.set(part, { name: part, path: current, children: new Map(), count: 0, last: null,
                                retain: false, el: null, open: current.split("/").length < 2 });
    }
    node = node.children.get(part);
  }
  return node;
}

function countUp(path, n) {
  let node = root;
  root.count += n;
  for (const part of path.split("/")) {
    node = node.children.get(part);
    node.count += n;
  }
}

function renderTree() {
  treeEl.textContent = "";
  const all = PC.el("li", {}, nodeRow(root, "All topics"));
  treeEl.append(all);
  appendChildren(treeEl, root);
}

function appendChildren(parentEl, node) {
  const names = [...node.children.keys()].sort();
  for (const name of names) {
    const child = node.children.get(name);
    const li = PC.el("li", {});
    li.append(nodeRow(child, child.name));
    if (child.children.size) {
      const ul = PC.el("ul", { class: "tree" + (child.open ? "" : " hidden") });
      appendChildren(ul, child);
      li.append(ul);
    }
    parentEl.append(li);
  }
}

function nodeRow(node, label) {
  const hasChildren = node.children.size > 0 && node !== root;
  const toggle = PC.el("span", { class: "toggle" }, hasChildren ? (node.open ? "▾" : "▸") : "");
  if (hasChildren) {
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      node.open = !node.open;
      renderTree();
    });
  }
  const count = PC.el("span", { class: "tag" }, String(node.count));
  const row = PC.el("div", { class: "node" + (node.path === selected ? " selected" : "") + (node.retain ? " retained" : "") },
                    toggle, PC.el("span", { class: "name" }, label), " ", count);
  row.addEventListener("click", () => select(node.path));
  node.el = row;
  node.countEl = count;
  return row;
}

function flash(path) {
  let node = root;
  const nodes = [root];
  for (const part of path.split("/")) {
    node = node.children.get(part);
    if (!node) return;
    nodes.push(node);
  }
  for (const n of nodes) {
    if (!n.countEl) continue;
    n.countEl.textContent = String(n.count);
    n.el.classList.remove("flash");
    void n.el.offsetWidth;   // restart the animation
    n.el.classList.add("flash");
  }
}

// --- messages ---

function matches(m) {
  if (hideNoisy.checked && NOISY.has(m.topic) && m.topic !== selected) return false;
  if (selected) {
    if (exactBox.checked ? m.topic !== selected : !(m.topic === selected || m.topic.startsWith(selected + "/"))) {
      return false;
    }
  }
  const q = filterInput.value.trim().toLowerCase();
  return !q || m.topic.toLowerCase().includes(q) || m.payload.toLowerCase().includes(q);
}

const COMPACT_MAX = 120;   // short JSON stays on one line

function pretty(m) {
  if (!m.json) return m.payload;
  try {
    const value = JSON.parse(m.payload);
    const compact = JSON.stringify(value);
    return compact.length <= COMPACT_MAX ? compact : format(value, "");
  } catch (err) {
    return m.payload;
  }
}

// Indented JSON, but small arrays/objects (e.g. boxes, coordinates) stay inline.
const INLINE_MAX = 70;
function format(value, indent) {
  const flat = JSON.stringify(value);
  if (value === null || typeof value !== "object" || flat.length <= INLINE_MAX) return flat;
  const inner = indent + "  ";
  if (Array.isArray(value)) {
    return "[\n" + value.map((v) => inner + format(v, inner)).join(",\n") + "\n" + indent + "]";
  }
  const entries = Object.entries(value).map(([k, v]) => `${inner}${JSON.stringify(k)}: ${format(v, inner)}`);
  return "{\n" + entries.join(",\n") + "\n" + indent + "}";
}

function messageItem(m) {
  const time = new Date(m.ts * 1000);
  const stamp = time.toLocaleTimeString([], { hour12: false }) + "." + String(time.getMilliseconds()).padStart(3, "0");
  return PC.el("li", {},
    PC.el("div", { class: "row" },
      PC.el("span", { class: "time" }, stamp),
      PC.el("strong", { class: "topic", onclick: () => select(m.topic) }, m.topic),
      m.retain ? PC.el("span", { class: "tag" }, "retained") : null,
      PC.el("span", { class: "tag" }, `QoS ${m.qos}`),
      PC.el("span", { class: "muted" }, `${m.size} B`)),
    PC.el("pre", { class: "payload" + (m.payload.length <= COMPACT_MAX ? " compact" : "") }, pretty(m)));
}

function renderPending() {
  if (paused || !pending.length) { pending.length = 0; return; }
  const fragment = document.createDocumentFragment();
  for (const m of pending.splice(0).reverse()) fragment.append(messageItem(m));
  list.prepend(fragment);
  while (list.children.length > MAX_SHOWN) list.lastChild.remove();
}

async function loadMessages() {
  const p = new URLSearchParams({ topic: selected, subtree: exactBox.checked ? "0" : "1", limit: "300" });
  const messages = await PC.api("GET", "/api/mqtt/messages?" + p);
  list.textContent = "";
  for (const m of messages) if (matches(m)) list.append(messageItem(m));
}

async function select(path) {
  selected = path;
  document.getElementById("selected").textContent = path || "All topics";
  renderTree();
  await loadMessages();
}

// --- wiring ---

async function loadTopics() {
  const data = await PC.api("GET", "/api/mqtt/topics");
  document.getElementById("prefix").textContent = data.prefix;
  for (const t of data.topics) {
    const node = nodeFor(t.topic);
    node.retain = t.retain;
    countUp(t.topic, t.count);
  }
  renderTree();
}

function connect() {
  const source = new EventSource("/api/mqtt/events");
  source.addEventListener("mqtt", (e) => {
    const m = JSON.parse(e.data);
    received += 1;
    const isNew = !hasPath(m.topic);
    const node = nodeFor(m.topic);
    node.retain = m.retain;
    countUp(m.topic, 1);
    if (isNew) renderTree(); else flash(m.topic);
    if (matches(m)) pending.push(m);
  });
}

function hasPath(path) {
  let node = root;
  for (const part of path.split("/")) {
    node = node.children.get(part);
    if (!node) return false;
  }
  return true;
}

document.getElementById("pause").addEventListener("click", (e) => {
  paused = !paused;
  e.target.textContent = paused ? "Resume" : "Pause";
  if (!paused) loadMessages();
});
document.getElementById("clear").addEventListener("click", async () => {
  await PC.api("POST", "/api/mqtt/clear");
  root.children.clear();
  root.count = 0;
  list.textContent = "";
  renderTree();
});
for (const el of [filterInput, exactBox, hideNoisy]) el.addEventListener("input", loadMessages);

setInterval(renderPending, 200);
setInterval(() => {
  document.getElementById("rate").textContent = `${(received / 2).toFixed(1)} msg/s`;
  received = 0;
}, 2000);

PC.init("mqtt").then(async () => {
  if (PC.me.role !== "admin") { location.href = "/"; return; }
  await loadTopics();
  await loadMessages();
  connect();
});
