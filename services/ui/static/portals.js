/**
 * Vectrax — Universe Portals
 * ============================
 * Starfield particle canvas + live stats from /v1/ API.
 *
 * API endpoints consumed:
 *   GET /v1/health         → uptime, version, env, system health
 *   GET /v1/memory/stats   → stars, constellations, pending proposals
 *   GET /v1/status         → governor, graph, autopatch
 *   GET /v1/auth/me        → identity (if logged in)
 */

const API = '/v1';

/* ==================================================================
   HELPERS
   ================================================================== */

function $(id) { return document.getElementById(id); }
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function getToken() { return localStorage.getItem('vx_token') || ''; }
function getUser()  { return JSON.parse(localStorage.getItem('vx_user') || 'null'); }

async function api(path) {
  const opts = { headers: {} };
  const token = getToken();
  if (token) opts.headers['Authorization'] = `Bearer ${token}`;
  try {
    const res = await fetch(API + path, opts);
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

/* ==================================================================
   1. STARFIELD — Animated particle background
   ================================================================== */

function initStarfield() {
  const canvas = $('starfield');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W, H;
  const stars = [];
  const STAR_COUNT = 200;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  // Create stars
  for (let i = 0; i < STAR_COUNT; i++) {
    stars.push({
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 1.4 + 0.3,
      vx: (Math.random() - 0.5) * 0.15,
      vy: (Math.random() - 0.5) * 0.15,
      alpha: Math.random() * 0.6 + 0.2,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: Math.random() * 0.02 + 0.005,
    });
  }

  // Color palette (matches portal colors)
  const colors = [
    'rgba(79,143,247,',   // accent
    'rgba(167,139,250,',  // cognition
    'rgba(52,211,153,',   // memory
    'rgba(251,191,36,',   // creator
    'rgba(6,182,212,',    // connections
    'rgba(209,213,219,',  // neutral
  ];

  function draw() {
    ctx.clearRect(0, 0, W, H);

    for (const s of stars) {
      s.x += s.vx;
      s.y += s.vy;
      s.pulse += s.pulseSpeed;

      // Wrap edges
      if (s.x < 0) s.x = W;
      if (s.x > W) s.x = 0;
      if (s.y < 0) s.y = H;
      if (s.y > H) s.y = 0;

      const brightness = s.alpha * (0.6 + 0.4 * Math.sin(s.pulse));
      const color = colors[Math.floor(s.x * colors.length / W)];

      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = color + brightness + ')';
      ctx.fill();
    }

    // Draw faint connection lines between close stars
    ctx.lineWidth = 0.3;
    for (let i = 0; i < stars.length; i++) {
      for (let j = i + 1; j < stars.length; j++) {
        const dx = stars[i].x - stars[j].x;
        const dy = stars[i].y - stars[j].y;
        const dist = dx * dx + dy * dy;
        if (dist < 6000) {
          const alpha = (1 - dist / 6000) * 0.12;
          ctx.beginPath();
          ctx.moveTo(stars[i].x, stars[i].y);
          ctx.lineTo(stars[j].x, stars[j].y);
          ctx.strokeStyle = `rgba(79,143,247,${alpha})`;
          ctx.stroke();
        }
      }
    }

    requestAnimationFrame(draw);
  }
  draw();
}

/* ==================================================================
   2. LOAD LIVE STATS — Populate portal stats from API
   ================================================================== */

async function loadStats() {
  // Health
  const health = await api('/health');
  if (health) {
    $('u-version').textContent = health.version || 'v1.0.0';
    $('u-uptime').textContent = health.uptime_seconds
      ? formatUptime(health.uptime_seconds)
      : '—';
    $('u-env').textContent = health.env || '—';

    const pulse = document.querySelector('.u-pulse');
    const label = $('u-health-label');
    if (health.status === 'ok' || health.status === 'healthy') {
      pulse.className = 'u-pulse ok';
      label.textContent = 'System Online';
      label.style.color = '#34d399';
    } else {
      pulse.className = 'u-pulse err';
      label.textContent = health.status || 'Degraded';
      label.style.color = '#f87171';
    }

    const ping = $('u-ping');
    ping.className = 'u-ping online';
  } else {
    $('u-health-label').textContent = 'Offline';
    document.querySelector('.u-pulse').className = 'u-pulse err';
    $('u-ping').className = 'u-ping offline';
  }

  // Memory stats
  const stats = await api('/memory/stats');
  if (stats) {
    $('stat-creator-stars').textContent = stats.stars || 0;
    $('stat-mem-stars').textContent = stats.stars || 0;
    $('stat-mem-const').textContent = stats.constellations || 0;
    $('stat-ctrl-proposals').textContent = stats.pending_proposals || 0;
  }

  // System status
  const status = await api('/status');
  if (status) {
    $('stat-ctrl-governor').textContent = status.governor_mode || '—';
    $('stat-ctrl-streak').textContent = status.clean_streak || 0;
    $('stat-mem-graph').textContent = (status.graph_nodes || 0) + 'n/' + (status.graph_edges || 0) + 'e';
  }

  // Sessions (from memory/sessions)
  const sessions = await api('/memory/sessions');
  if (sessions && sessions.sessions) {
    $('stat-creator-sessions').textContent = sessions.sessions.length;
  }

  // Placeholders for intelligence & connections (gracefully handle missing endpoints)
  const intel = await api('/status/intelligence');
  if (intel) {
    $('stat-intel-providers').textContent = intel.providers || '—';
    $('stat-intel-status').textContent = intel.status || '—';
  } else {
    $('stat-intel-providers').textContent = '—';
    $('stat-intel-status').textContent = 'offline';
  }

  const conn = await api('/status/connectors');
  if (conn) {
    $('stat-conn-total').textContent = conn.total || '—';
    $('stat-conn-healthy').textContent = conn.healthy || '—';
  } else {
    $('stat-conn-total').textContent = '—';
    $('stat-conn-healthy').textContent = '—';
  }

  // Cognition stats
  $('stat-cog-proposals').textContent = (stats && stats.pending_proposals) || 0;
  $('stat-cog-mode').textContent = (status && status.governor_mode) || 'supervised';
}

/* ==================================================================
   3. IDENTITY — Show logged-in user
   ================================================================== */

async function loadIdentity() {
  const user = getUser();
  const token = getToken();
  const el = $('u-identity');

  if (!token || !user) {
    el.innerHTML = '<a href="/" style="color:#4f8ff7;text-decoration:none">Sign in</a>';
    return;
  }

  // Validate session
  const me = await api('/auth/me');
  if (me) {
    el.innerHTML = `<strong>${esc(me.username)}</strong> <span style="color:#5a6374">${me.role}</span>`;
  } else {
    el.innerHTML = `<strong>${esc(user.username)}</strong> <span style="color:#5a6374">(session expired)</span>`;
  }
}

/* ==================================================================
   4. PORTAL CLICK HANDLING — Transition effect
   ================================================================== */

function initPortalClicks() {
  document.querySelectorAll('.portal').forEach(portal => {
    portal.addEventListener('click', function(e) {
      e.preventDefault();
      const href = this.getAttribute('href');

      // Add a brief "entering portal" visual effect
      this.style.transition = 'transform 0.3s, opacity 0.3s';
      this.style.transform = 'scale(1.08)';
      this.style.opacity = '0.7';

      // Navigate after brief delay
      setTimeout(() => {
        window.location.href = href;
      }, 250);
    });
  });
}

/* ==================================================================
   5. UTILITIES
   ================================================================== */

function formatUptime(seconds) {
  if (seconds < 60) return Math.round(seconds) + 's';
  if (seconds < 3600) return Math.round(seconds / 60) + 'm';
  if (seconds < 86400) return Math.round(seconds / 3600) + 'h';
  return Math.round(seconds / 86400) + 'd';
}

/* ==================================================================
   6. INIT
   ================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  initStarfield();
  initPortalClicks();
  loadIdentity();
  loadStats();

  // Refresh stats every 30 seconds
  setInterval(loadStats, 30000);
});
