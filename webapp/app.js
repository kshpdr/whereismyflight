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

  let currentDate = null;
  let availableDates = [];

  const flight = getFlightParam();
  if (!flight) {
    showError("No flight number provided.");
  } else {
    loadFlight(flight);
    setInterval(() => loadFlight(flight, currentDate), REFRESH_MS);
  }

  // ── Data ────────────────────────────────────────────────────────

  function getFlightParam() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("flight") || "";

    const fromTg = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "";

    return (fromUrl || fromTg).trim().toUpperCase() || null;
  }

  async function loadFlight(code, date) {
    try {
      let url = `${API_BASE}/api/flight/${encodeURIComponent(code)}`;
      if (date) url += `?date=${date}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      currentDate = data.date || null;
      availableDates = data.available_dates || [];
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

    renderDateNav(d.date, d.available_dates || []);

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

  function renderDateNav(selectedDate, dates) {
    let nav = $("#date-nav");
    if (!nav) {
      nav = document.createElement("div");
      nav.id = "date-nav";
      nav.className = "date-nav";
      const header = $(".flight-header");
      if (header) header.after(nav);
    }

    if (!dates || dates.length <= 1) {
      nav.style.display = "none";
      return;
    }

    nav.style.display = "flex";
    nav.innerHTML = "";

    dates.forEach((d) => {
      const btn = document.createElement("button");
      btn.className = "date-btn" + (d === selectedDate ? " active" : "");
      const dt = new Date(d + "T12:00:00Z");
      const label = dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      btn.textContent = label;
      btn.onclick = () => {
        currentDate = d;
        loadFlight(flight, d);
      };
      nav.appendChild(btn);
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
        <div class="plane-icon" data-leg="${index}">✈️</div>
      </div>
      <div class="time-row">
        <span class="time">${shortTime(depTime)}${dep.tz ? `<span class="tz-label"> ${esc(dep.tz)}</span>` : ""}</span>
        <span class="duration">${formatMinutes(leg.duration_min)}</span>
        <span class="time">${shortTime(arrTime)}${arr.tz ? `<span class="tz-label"> ${esc(arr.tz)}</span>` : ""}</span>
      </div>
      ${leg.status === "In Air" && leg.remaining_min != null ? `<div class="remaining-label">${formatMinutes(leg.remaining_min)} remaining</div>` : ""}
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

    const pct = leg.progress_pct ?? 0;

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
    const m = iso.match(/T(\d{2}):(\d{2})/);
    return m ? `${m[1]}:${m[2]}` : "—";
  }

  function formatMinutes(minutes) {
    if (!minutes || minutes <= 0) return "—";
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
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
