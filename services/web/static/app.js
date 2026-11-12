// Shared by all pages: session, API calls, header, emergency stop, live events.
"use strict";

const PC = {
  me: null,
  handlers: {},

  async init(page) {
    const res = await fetch("/api/me");
    if (res.status === 401) { location.href = "/login"; return; }
    PC.me = await res.json();
    PC.header(page);
    PC.connect();
  },

  async api(method, url, body) {
    const opts = { method, headers: {} };
    if (method !== "GET") {
      opts.headers["X-CSRF-Token"] = PC.me.csrf;
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body || {});
    }
    const res = await fetch(url, opts);
    if (res.status === 401) { location.href = "/login"; throw new Error("login required"); }
    const type = res.headers.get("Content-Type") || "";
    const data = type.includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw new Error((data && data.error) || data || res.statusText);
    return data;
  },

  on(event, fn) { (PC.handlers[event] = PC.handlers[event] || []).push(fn); },

  connect() {
    const source = new EventSource("/events");
    for (const event of ["status", "history", "detections", "log", "gamepad", "bluetooth", "agent", "display", "map"]) {
      source.addEventListener(event, (e) => {
        const data = JSON.parse(e.data);
        for (const fn of PC.handlers[event] || []) fn(data);
      });
    }
    source.onerror = () => PC.setLink(false);
    source.onopen = () => PC.setLink(true);
  },

  setLink(ok) {
    const el = document.getElementById("link");
    if (el) {
      el.className = "live-dot " + (ok ? "ok" : "bad");
      el.title = ok ? "live connection" : "connection lost";
    }
  },

  // Light/dark like bernd-zuther.de: explicit choice wins over the system.
  toggleTheme() {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark"
                                    : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("picrawler-theme", root.dataset.theme); } catch (err) { /* ignore */ }
    PC.updateThemeButton();
  },

  updateThemeButton() {
    const button = document.getElementById("theme");
    if (!button) return;
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark"
                                    : matchMedia("(prefers-color-scheme: dark)").matches;
    button.textContent = dark ? "☾" : "☀";
    button.title = dark ? "Switch to light mode" : "Switch to dark mode";
  },

  header(page) {
    const header = document.createElement("header");
    header.innerHTML = `
      <a class="brand" href="/">
        <img class="brand-mark" src="/static/logo-mark.svg" alt="">
        <span class="name">PiCrawler<span class="sub"><span id="link" class="live-dot"></span>picrawler@robot:~</span></span>
      </a>
      <nav>
        <a href="/" data-page="live">Live</a>
        <a href="/log" data-page="log">Log</a>
        <a href="/remote" data-page="remote" class="admin-only">Remote</a>
        <a href="/admin" data-page="admin" class="admin-only">Admin</a>
        <a href="/mqtt" data-page="mqtt" class="admin-only">MQTT</a>
      </nav>
      <span class="battery hidden" id="battery"></span>
      <span class="user"></span>
      <button id="theme" class="theme-toggle">☀</button>
      <button id="logout">Log out</button>
      <button class="stop" id="stop" title="Stop all motion now">STOP</button>`;
    document.body.prepend(header);
    header.querySelector(".user").textContent = `${PC.me.user} (${PC.me.role})`;
    for (const a of header.querySelectorAll("nav a")) {
      if (a.dataset.page === page) a.classList.add("active");
      if (a.classList.contains("admin-only") && PC.me.role !== "admin") a.remove();
    }
    header.querySelector("#stop").addEventListener("click", () => PC.stop(false));
    header.querySelector("#theme").addEventListener("click", PC.toggleTheme);
    PC.on("status", (s) => PC.showBattery(s.battery));
    PC.updateThemeButton();
    header.querySelector("#logout").addEventListener("click", async () => {
      await PC.api("POST", "/logout");
      location.href = "/login";
    });
    document.addEventListener("keydown", (e) => {
      // Escape anywhere = emergency stop, unless typing in a field or closing
      // a dialog (dialogs have their own STOP button)
      if (e.key === "Escape" && !document.querySelector("dialog[open]") &&
          !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
        PC.stop(false);
      }
    });
  },

  // Battery pill in the header, on every page.
  showBattery(b) {
    const el = document.getElementById("battery");
    if (!el) return;
    if (!b || !b.present) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    el.className = "battery " + (b.level || "ok");
    el.textContent = "";
    const fill = PC.el("span", { class: "battery-fill" });
    fill.style.width = `${Math.max(0, Math.min(100, b.percent))}%`;
    el.append(PC.el("span", { class: "battery-icon" }, fill), `${b.percent}%`);
    el.title = `${b.voltage.toFixed(2)} V, ${b.level}`;
  },

  // OLED mirror: 1-bit frame (8 px per byte, MSB = leftmost, 1 = lit).
  drawOled(canvas, frame) {
    const ctx = canvas.getContext("2d");
    const bytes = Uint8Array.from(atob(frame.data), (c) => c.charCodeAt(0));
    const image = ctx.createImageData(frame.w, frame.h);
    const rowBytes = Math.ceil(frame.w / 8);
    for (let y = 0; y < frame.h; y++) {
      for (let x = 0; x < frame.w; x++) {
        const lit = bytes[y * rowBytes + (x >> 3)] & (0x80 >> (x & 7));
        const i = (y * frame.w + x) * 4;
        image.data[i] = lit ? 232 : 6;
        image.data[i + 1] = lit ? 246 : 8;
        image.data[i + 2] = lit ? 255 : 18;
        image.data[i + 3] = 255;
      }
    }
    canvas.width = frame.w;
    canvas.height = frame.h;
    ctx.putImageData(image, 0, 0);
  },

  async stop(sit) {
    try {
      await PC.api("POST", "/api/stop", { sit });
      PC.toast(sit ? "Stop sent, sitting down" : "Stop sent");
    } catch (err) {
      PC.toast("Stop failed: " + err.message, true);
    }
  },

  toast(message, error) {
    const el = document.createElement("div");
    el.className = "toast" + (error ? " error" : "");
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), error ? 6000 : 2500);
  },

  time(ts) {
    if (ts == null) return "";
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },

  dateTime(ts) {
    if (ts == null) return "";
    return new Date(ts * 1000).toLocaleString();
  },

  // "person 14:02:10–14:02:31 · 2× · 0.91" / "stop 14:03:00 · obstacle"
  describe(row) {
    if (row.kind === "sighting") {
      const span = row.end ? `${PC.time(row.start)} – ${PC.time(row.end)}` : `${PC.time(row.start)} – now`;
      const count = row.max_count > 1 ? ` · ${row.max_count}×` : "";
      return { title: row.label, text: `${span}${count} · ${Number(row.max_score).toFixed(2)}` };
    }
    return { title: row.label, text: `${PC.time(row.start)} · ${row.detail || ""}` };
  },

  el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const child of children) {
      if (child != null) node.append(child instanceof Node ? child : String(child));
    }
    return node;
  },
};
