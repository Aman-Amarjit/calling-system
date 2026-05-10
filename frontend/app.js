// Live monitor — polls the FastAPI backend every 3 seconds
// API_BASE auto-detects: same origin when served by FastAPI, localhost for local file open
const API_BASE = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" || window.location.protocol === "file:")
  ? "http://localhost:8000"
  : window.location.origin;

const elActiveCalls   = document.getElementById("active-calls");
const elBookings      = document.getElementById("recent-bookings");
const elServerStatus  = document.getElementById("server-status");

// Recent bookings stored in memory for this session
const recentBookings = [];

async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
    if (res.ok) {
      elServerStatus.textContent = "Online";
      elServerStatus.className = "status-dot status-online";
      return true;
    }
  } catch (_) {}
  elServerStatus.textContent = "Offline";
  elServerStatus.className = "status-dot status-offline";
  return false;
}

async function fetchStats() {
  try {
    const res = await fetch(`${API_BASE}/stats`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) return;
    const data = await res.json();

    // Active calls
    elActiveCalls.textContent = data.active_calls ?? "0";

    // New bookings
    if (data.latest_booking) {
      const b = data.latest_booking;
      const key = `${b.timestamp}-${b.name}`;
      if (!recentBookings.find(x => x.key === key)) {
        recentBookings.unshift({ key, ...b });
        if (recentBookings.length > 5) recentBookings.pop();
        renderBookings();
      }
    }
  } catch (_) {}
}

function renderBookings() {
  if (recentBookings.length === 0) {
    elBookings.innerHTML = '<p class="monitor-hint">No bookings yet this session.</p>';
    return;
  }
  elBookings.innerHTML = recentBookings.map(b => `
    <div class="booking-entry">
      <strong>${b.name || "—"}</strong> &middot; ${b.date || "—"} at ${b.time || "—"}<br>
      <span style="color:#6b7280;font-size:12px">${b.phone || ""} &middot; ${b.timestamp || ""}</span>
    </div>
  `).join("");
}

async function poll() {
  const online = await checkHealth();
  if (online) await fetchStats();
}

// Poll immediately, then every 3 seconds
poll();
setInterval(poll, 3000);
