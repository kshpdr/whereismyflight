/* global Telegram */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const tg = window.Telegram && Telegram.WebApp;

  if (tg) {
    tg.ready();
    tg.expand();
    applyTelegramTheme();
  }

  const API_BASE = window.location.origin;
  const REFRESH_MS = 60_000;

  const flight = getFlightParam();
  if (!flight) {
    showError("No flight number provided.");
  } else {
    loadFlight(flight);
    setInterval(() => loadFlight(flight), REFRESH_MS);
  }

  // ── Data ────────────────────────────────────────────────────────

  function getFlightParam() {
    const params = new URLSearchParams(window.location.search);
    return (params.get("flight") || "").trim().toUpperCase() || null;
  }

  async function loadFlight(code) {
    try {
      const resp = await fetch(`${API_BASE}/api/flight/${encodeURIComponent(code)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      render(data);
    } catch (err) {
      console.error("Failed to load flight:", err);
      showError("Couldn't load flight data. Please try again later.");
    }
  }

  // ── Rendering ───────────────────────────────────────────────────

  function render(d) {
    showScreen("flight-screen");

    $("#flight-number").textContent = formatFlightCode(d.flight);
    $("#airline-name").textContent = d.airline || "";

    renderStatusBadge(d.status, d.legs);

    if (d.demo) {
      $("#demo-tag").classList.add("visible");
    } else {
      $("#demo-tag").classList.remove("visible");
    }

    const container = $("#legs-container");
    container.innerHTML = "";

    const legs = d.legs || [];

    if (legs.length > 1) {
      container.appendChild(buildRouteSummary(legs));
    }

    legs.forEach((leg, i) => {
      container.appendChild(buildLegCard(leg, i, legs.length));
    });
  }

  function buildRouteSummary(legs) {
    const el = document.createElement("div");
    el.className = "route-summary";
    const stops = [legs[0].departure.iata];
    legs.forEach((l) => stops.push(l.arrival.iata));
    el.innerHTML = `<span class="route-path">${stops.join('<span class="route-arrow"> → </span>')}</span>`;
    return el;
  }

  function buildLegCard(leg, index, total) {
    const wrapper = document.createElement("div");
    wrapper.className = "leg-wrapper";

    if (total > 1) {
      const label = document.createElement("div");
      label.className = "leg-label";
      const statusCls = legStatusClass(leg.status);
      label.innerHTML = `<span class="leg-num">Leg ${index + 1}</span><span class="leg-status ${statusCls}">${leg.status}</span>`;
      wrapper.appendChild(label);
    }

    const dep = leg.departure;
    const arr = leg.arrival;
    const depTime = dep.estimated || dep.scheduled;
    const arrTime = arr.estimated || arr.scheduled;

    const card = document.createElement("div");
    card.className = "route-card glass";
    card.innerHTML = `
      <div class="route-endpoints">
        <div class="endpoint dep">
          <span class="iata">${esc(dep.iata) || "—"}</span>
          <span class="city">${esc(dep.airport) || ""}</span>
        </div>
        <div class="endpoint arr">
          <span class="iata">${esc(arr.iata) || "—"}</span>
          <span class="city">${esc(arr.airport) || ""}</span>
        </div>
      </div>
      <div class="progress-track">
        <div class="track-line"></div>
        <div class="track-fill" data-leg="${index}"></div>
        <div class="plane-icon" data-leg="${index}">
          <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
            <path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 10-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>
          </svg>
        </div>
      </div>
      <div class="time-row">
        <span class="time">${shortTime(depTime)}</span>
        <span class="duration">${calcDuration(depTime, arrTime)}</span>
        <span class="time">${shortTime(arrTime)}</span>
      </div>
    `;
    wrapper.appendChild(card);

    const grid = document.createElement("div");
    grid.className = "detail-grid";
    grid.innerHTML = `
      <div class="detail-card glass">
        <div class="detail-label">Departure</div>
        ${detailRow("Terminal", dep.terminal)}
        ${detailRow("Gate", dep.gate)}
        ${detailRow("Scheduled", shortTime(dep.scheduled))}
        ${dep.delay && parseInt(dep.delay) > 0 ? detailRow("Delay", `+${dep.delay} min`, true) : ""}
      </div>
      <div class="detail-card glass">
        <div class="detail-label">Arrival</div>
        ${detailRow("Terminal", arr.terminal)}
        ${detailRow("Gate", arr.gate)}
        ${detailRow("Scheduled", shortTime(arr.scheduled))}
        ${arr.delay && parseInt(arr.delay) > 0 ? detailRow("Delay", `+${arr.delay} min`, true) : ""}
      </div>
    `;
    wrapper.appendChild(grid);

    requestAnimationFrame(() => animateProgress(leg, index));

    return wrapper;
  }

  function detailRow(key, val, isDelay) {
    if (!val) return "";
    return `<div class="detail-row"><span class="detail-key">${esc(key)}</span><span class="detail-val${isDelay ? " delay-val" : ""}">${esc(String(val))}</span></div>`;
  }

  function renderStatusBadge(status, legs) {
    const badge = $("#status-badge");
    badge.className = "badge";

    const hasDelay = (legs || []).some((l) => parseInt(l.departure.delay, 10) > 0);

    if (hasDelay && status !== "Landed" && status !== "Cancelled") {
      badge.classList.add("delayed");
      badge.textContent = "Delayed";
    } else {
      const map = {
        "In Air":    { cls: "in-air",    label: "In Air" },
        "En Route":  { cls: "in-air",    label: "En Route" },
        "Landed":    { cls: "landed",    label: "Landed" },
        "Cancelled": { cls: "cancelled", label: "Cancelled" },
        "Diverted":  { cls: "cancelled", label: "Diverted" },
      };
      const m = map[status];
      if (m) {
        badge.classList.add(m.cls);
        badge.textContent = m.label;
      } else {
        badge.textContent = "On Time";
      }
    }
  }

  function animateProgress(leg, index) {
    const fill = document.querySelector(`.track-fill[data-leg="${index}"]`);
    const plane = document.querySelector(`.plane-icon[data-leg="${index}"]`);
    if (!fill || !plane) return;

    const depISO = leg.departure.estimated || leg.departure.scheduled;
    const arrISO = leg.arrival.estimated || leg.arrival.scheduled;

    let pct = 0;
    if (leg.status === "Landed") {
      pct = 100;
    } else if (leg.status === "In Air" && depISO && arrISO) {
      const depMs = new Date(depISO).getTime();
      const arrMs = new Date(arrISO).getTime();
      const total = arrMs - depMs;
      if (total > 0) {
        pct = Math.max(0, Math.min(100, ((Date.now() - depMs) / total) * 100));
      }
    }

    fill.style.width = pct + "%";
    plane.style.left = pct + "%";
  }

  function legStatusClass(status) {
    return {
      "In Air": "status-air",
      "Landed": "status-landed",
      "Cancelled": "status-cancelled",
      "Scheduled": "status-scheduled",
    }[status] || "status-scheduled";
  }

  // ── Utilities ───────────────────────────────────────────────────

  function formatFlightCode(code) {
    if (!code) return "";
    const m = code.match(/^([A-Z]{2})(\d+)$/);
    return m ? `${m[1]} ${m[2]}` : code;
  }

  function shortTime(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    } catch {
      return iso.slice(11, 16);
    }
  }

  function calcDuration(depISO, arrISO) {
    if (!depISO || !arrISO) return "—";
    const ms = new Date(arrISO) - new Date(depISO);
    if (ms <= 0) return "—";
    const h = Math.floor(ms / 3_600_000);
    const m = Math.round((ms % 3_600_000) / 60_000);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  function esc(s) {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function showScreen(id) {
    document.querySelectorAll(".screen").forEach((s) => {
      s.classList.remove("active");
      s.style.display = "none";
    });
    const target = document.getElementById(id);
    if (target) {
      target.classList.add("active");
      target.style.display = "block";
    }
  }

  function showError(msg) {
    $("#error-message").textContent = msg;
    showScreen("error-screen");
  }

  function applyTelegramTheme() {
    if (!tg) return;
    const root = document.documentElement;
    const tp = tg.themeParams || {};
    if (tp.bg_color) root.style.setProperty("--bg", tp.bg_color);
    if (tp.secondary_bg_color) root.style.setProperty("--surface", hexToRGBA(tp.secondary_bg_color, 0.5));
    if (tp.text_color) root.style.setProperty("--text", tp.text_color);
    if (tp.hint_color) root.style.setProperty("--text-dim", tp.hint_color);
    if (tp.button_color) root.style.setProperty("--accent", tp.button_color);
  }

  function hexToRGBA(hex, alpha) {
    const h = hex.replace("#", "");
    return `rgba(${parseInt(h.substring(0, 2), 16)}, ${parseInt(h.substring(2, 4), 16)}, ${parseInt(h.substring(4, 6), 16)}, ${alpha})`;
  }
})();
