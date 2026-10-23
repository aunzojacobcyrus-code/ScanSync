/* ==========================================================================
   ScanSync — shared UI helpers
   window.ScanSync = { api, toast, csrf, fmtTime, escape }
   ========================================================================== */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // ---- Theme (persisted, respects OS preference) -------------------------
  const THEME_KEY = "scansync-theme";
  const applyTheme = (t) => {
    document.documentElement.dataset.theme = t;
    $$("[data-theme-icon]").forEach((el) => (el.hidden = el.dataset.themeIcon !== t));
  };
  applyTheme(localStorage.getItem(THEME_KEY) ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-theme-toggle]")) return;
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });

  // ---- Mobile sidebar ----------------------------------------------------
  document.addEventListener("click", (e) => {
    const app = $(".app");
    if (!app) return;
    if (e.target.closest("[data-nav-toggle]")) app.classList.toggle("nav-open");
    else if (app.classList.contains("nav-open") && !e.target.closest(".sidebar")) app.classList.remove("nav-open");
  });

  // ---- Sticky topbar shadow ---------------------------------------------
  const topbar = $(".topbar");
  if (topbar) {
    const onScroll = () => topbar.classList.toggle("is-scrolled", scrollY > 4);
    addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ---- Live clock --------------------------------------------------------
  const clock = $("[data-clock]");
  if (clock) {
    const tick = () => (clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    tick();
    setInterval(tick, 1000);
  }

  // ---- Confirm before destructive submits -------------------------------
  document.addEventListener("submit", (e) => {
    const msg = e.target.dataset.confirm;
    if (msg && !confirm(msg)) e.preventDefault();
  });

  // ---- <dialog> openers --------------------------------------------------
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-dialog]");
    if (opener) $(opener.dataset.openDialog)?.showModal();
    const closer = e.target.closest("[data-close-dialog]");
    if (closer) closer.closest("dialog")?.close();
  });

  // ---- Auto-submit filter forms -----------------------------------------
  $$("form[data-autosubmit]").forEach((form) => {
    form.addEventListener("change", (e) => { if (e.target.matches("select, input[type=date]")) form.requestSubmit(); });
  });

  // ---- Toasts ------------------------------------------------------------
  const ICONS = {
    success: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
    error: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    warning: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  };
  const svg = (paths, size = 20) =>
    `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;

  function toast(message, type = "info", ms = 3800) {
    let host = $(".toasts");
    if (!host) { host = document.createElement("div"); host.className = "toasts"; host.setAttribute("aria-live", "polite"); document.body.append(host); }
    const el = document.createElement("div");
    el.className = `toast toast--${type}`;
    el.innerHTML = `${svg(ICONS[type] || ICONS.info)}<span>${escape(message)}</span>`;
    host.append(el);
    setTimeout(() => { el.classList.add("is-leaving"); el.addEventListener("animationend", () => el.remove(), { once: true }); }, ms);
  }

  // ---- Fetch wrapper with CSRF ------------------------------------------
  function csrf() {
    return $('meta[name="csrf-token"]')?.content ||
      document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] || "";
  }

  async function api(url, { method = "GET", body, signal } = {}) {
    const res = await fetch(url, {
      method,
      signal,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  function escape(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });

  window.ScanSync = { $, $$, api, toast, csrf, escape, fmtTime, svg };
})();
