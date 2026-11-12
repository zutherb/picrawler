// Applies the saved light/dark choice before the page paints (no flash).
"use strict";
try {
  const theme = localStorage.getItem("picrawler-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch (err) { /* storage blocked: follow the system setting */ }
