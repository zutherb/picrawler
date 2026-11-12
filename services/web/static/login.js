"use strict";
const reason = new URLSearchParams(location.search).get("error");
if (reason) {
  const el = document.getElementById("error");
  el.textContent = reason === "rate" ? "Too many attempts, wait a minute." : "Wrong user or password.";
  el.classList.remove("hidden");
}
