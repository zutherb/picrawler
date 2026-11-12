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
    for (const event of ["status", "history", "detections", "log", "gamepad", "bluetooth"]) {
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
    if (el) el.className = "dot " + (ok ? "ok" : "bad");
  },

  header(page) {
    const header = document.createElement("header");
    header.innerHTML = `
      <span class="brand"><span id="link" class="dot"></span>PiCrawler</span>
      <nav>
        <a href="/" data-page="live">Live</a>
        <a href="/log" data-page="log">Log</a>
        <a href="/admin" data-page="admin" class="admin-only">Admin</a>
      </nav>
      <span class="user"></span>
      <button id="logout">Log out</button>
      <button class="stop" id="stop" title="Stop all motion now">STOP</button>`;
    document.body.prepend(header);
    header.querySelector(".user").textContent = `${PC.me.user} (${PC.me.role})`;
    for (const a of header.querySelectorAll("nav a")) {
      if (a.dataset.page === page) a.classList.add("active");
      if (a.classList.contains("admin-only") && PC.me.role !== "admin") a.remove();
    }
    header.querySelector("#stop").addEventListener("click", () => PC.stop(false));
    header.querySelector("#logout").addEventListener("click", async () => {
      await PC.api("POST", "/logout");
      location.href = "/login";
    });
    document.addEventListener("keydown", (e) => {
      // Escape anywhere = emergency stop, unless typing in a field
      if (e.key === "Escape" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
        PC.stop(false);
      }
    });
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
      const span = row.end ? `${PC.time(row.start)}–${PC.time(row.end)}` : `${PC.time(row.start)}– now`;
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
