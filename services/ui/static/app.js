/**
 * Vectrax — Product Interface
 * ============================
 * 3-view SPA: Login → Chat → Dashboard
 * Consumes exclusively /v1/ API endpoints. Zero backend duplication.
 *
 * API endpoints used:
 *   POST /v1/auth/login          GET /v1/auth/me        POST /v1/auth/logout
 *   POST /v1/chat                GET /v1/memory/stars    GET /v1/memory/constellations
 *   GET /v1/memory/history       GET /v1/memory/sessions GET /v1/memory/stats
 *   GET /v1/proposals            POST /v1/proposals/:id/approve|reject
 *   GET /v1/status               GET /v1/status/operator GET /v1/status/audit
 *   GET /v1/health
 */

/* ==================================================================
   API CLIENT
   ================================================================== */

const API = '/v1';
let TOKEN = localStorage.getItem('vx_token') || '';
let USER  = JSON.parse(localStorage.getItem('vx_user') || 'null');

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (TOKEN) opts.headers['Authorization'] = `Bearer ${TOKEN}`;
  if (body)  opts.body = JSON.stringify(body);
  const res  = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function $(id) { return document.getElementById(id); }

/* ==================================================================
   1. LOGIN / LOGOUT / SESSION
   ================================================================== */

async function doLogin() {
  const user = $('login-user').value.trim();
  const pass = $('login-pass').value;
  const err  = $('login-error');
  err.textContent = ''; err.style.display = 'none';

  if (!user || !pass) { err.textContent = 'Username and password required'; err.style.display = 'block'; return; }

  try {
    const d = await api('POST', '/auth/login', { username: user, password: pass });
    TOKEN = d.token;
    USER  = { username: d.username, role: d.role, channel: d.channel };
    localStorage.setItem('vx_token', TOKEN);
    localStorage.setItem('vx_user', JSON.stringify(USER));
    enterApp();
  } catch (e) {
    err.textContent = e.message === 'Invalid credentials'
      ? 'Wrong username or password'
      : e.message;
    err.style.display = 'block';
  }
}

function doLogout() {
  api('POST', '/auth/logout').catch(() => {});
  TOKEN = ''; USER = null;
  localStorage.removeItem('vx_token');
  localStorage.removeItem('vx_user');
  $('view-app').style.display = 'none';
  $('view-login').style.display = 'flex';
  $('login-user').value = ''; $('login-pass').value = '';
  $('login-error').style.display = 'none';
  $('chat-messages').innerHTML = '';
}

async function enterApp() {
  $('view-login').style.display = 'none';
  $('view-app').style.display   = 'block';

  // Fetch full identity from /v1/auth/me
  try {
    const me = await api('GET', '/auth/me');
    USER.id = me.id; USER.is_active = me.is_active; USER.created_at = me.created_at;
    $('topbar-identity').innerHTML =
      `<strong>${esc(me.username)}</strong> <span class="tag tag-${me.role}">${me.role}</span> <span class="dim">${me.channel}</span>`;
  } catch {
    $('topbar-identity').textContent = `${USER.username} (${USER.role})`;
  }

  // Show chat context bar
  $('chat-context').innerHTML =
    `<span class="dim">Signed in as</span> <strong>${esc(USER.username)}</strong>
     <span class="tag tag-${USER.role}">${USER.role}</span>
     <span class="dim">${USER.channel}</span>`;

  // Auto-load chat history
  loadChatHistory();
}

/* ==================================================================
   2. NAVIGATION (Chat / Dashboard)
   ================================================================== */

function showPanel(name) {
  document.querySelectorAll('.main-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b  => b.classList.remove('active'));
  $('panel-' + name).classList.add('active');
  document.querySelector(`.nav-btn[data-view="${name}"]`).classList.add('active');

  if (name === 'dashboard') loadDashboard();
  if (name === 'chat')      loadChatHistory();
}

let _sistemaTimer = null;

function showDashSection(sec) {
  // Detener auto-refresh del sistema si cambiamos de tab
  if (sec !== 'sistema') { clearInterval(_sistemaTimer); _sistemaTimer = null; }

  document.querySelectorAll('.dash-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.dash-tab').forEach(b   => b.classList.remove('active'));
  $('sec-' + sec).classList.add('active');
  document.querySelector(`.dash-tab[data-section="${sec}"]`).classList.add('active');

  // Load section data on switch
  const loaders = { overview: loadOverview, gravity: loadGravity,
    mercado: loadMercado, convergencias: loadConvergencias,
    stars: loadStars, constellations: loadConstellations,
    history: loadHistory, sessions: loadSessions, proposals: loadProposals,
    operator: loadOperator, audit: loadAudit,
    sistema: loadSistema, ideas: loadIdeas };
  if (loaders[sec]) loaders[sec]();
}

/* ==================================================================
   3. CHAT  (POST /v1/chat + GET /v1/memory/history)
   ================================================================== */

function addBubble(html, cls) {
  const el = document.createElement('div');
  el.className = 'bubble ' + cls;
  el.innerHTML = html;
  const c = $('chat-messages');
  c.appendChild(el);
  c.scrollTop = c.scrollHeight;
}

/* --- Debug mode (hidden by default, Ctrl+Shift+D for owner/creator) --- */
let DEBUG_MODE = localStorage.getItem('vx_debug') === '1';

function toggleDebug() {
  if (!USER || (USER.role !== 'owner' && USER.channel !== 'creator')) return;
  DEBUG_MODE = !DEBUG_MODE;
  localStorage.setItem('vx_debug', DEBUG_MODE ? '1' : '0');
  const label = DEBUG_MODE ? 'DEBUG ON' : 'DEBUG OFF';
  addBubble(`<span class="tag tag-debug">${label}</span>`, 'bubble-sys');
}

document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.shiftKey && e.key === 'D') { e.preventDefault(); toggleDebug(); }
});

async function sendChat() {
  const input = $('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addBubble(esc(text), 'bubble-user');

  try {
    const d = await api('POST', '/chat', { text });
    const mode = d.resolve_mode || 'memory';
    const sovereign = d.sovereign_answer || '';

    if (mode === 'memory') {
      // --- Sovereign: brief confirmation ---
      let html = `<span class="sovereign-confirm">${esc(sovereign || 'Registrado.')}</span>`;
      // Debug details (hidden unless debug mode)
      if (DEBUG_MODE) {
        const status = d.is_duplicate ? 'Updated (near-duplicate)' : 'Ingested';
        const cls = d.is_duplicate ? 'tag-warn' : 'tag-ok';
        html += `<div class="debug-info">`;
        html += `<span class="tag tag-mode-memory">MEMORY</span> `;
        html += `<span class="tag ${cls}">${status}</span> `;
        html += `<span class="dim">${esc(d.layer)} \u00b7 gravity ${d.gravity_score} \u00b7 rep ${d.repetition_count}</span>`;
        html += `<div class="bubble-id">${d.star_id.substring(0, 12)}\u2026</div>`;
        html += `</div>`;
      }
      addBubble(html, 'bubble-sys');
    } else {
      // --- Sovereign: clean answer only ---
      let html = `<div class="sovereign-answer">${esc(sovereign)}</div>`;
      // Debug details (hidden unless debug mode)
      if (DEBUG_MODE) {
        const modeCls = mode === 'online' ? 'tag-mode-online' : 'tag-mode-local';
        html += `<div class="debug-info">`;
        html += `<span class="tag ${modeCls}">${mode.toUpperCase()}</span>`;
        if (d.fallback_from) html += ` <span class="tag tag-fallback">FALLBACK \u2190 ${d.fallback_from.toUpperCase()}</span>`;
        if (d.context_stars > 0) html += ` <span class="dim">${d.context_stars} stars</span>`;
        if (d.search_query) html += ` <span class="dim">q: ${esc(d.search_query).substring(0, 50)}</span>`;
        // Sources panel (debug only)
        if (d.sources && d.sources.length > 0) {
          html += '<div class="sources-panel">';
          html += '<div class="sources-title">Sources (debug)</div>';
          d.sources.forEach(s => {
            html += `<div class="source-card">`;
            html += `<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`;
            html += `<div class="source-snippet">${esc(s.snippet)}</div>`;
            html += `</div>`;
          });
          html += '</div>';
        }
        html += `</div>`;
      }
      addBubble(html, 'bubble-sys bubble-answer');
    }
  } catch (e) {
    addBubble(`<span class="tag tag-err">Error</span> ${esc(e.message)}`, 'bubble-sys');
  }
}

async function loadChatHistory() {
  const c = $('chat-messages');
  if (c.children.length > 0) return; // already has messages
  try {
    const d = await api('GET', '/memory/history?limit=30');
    const msgs = d.messages || [];
    if (msgs.length === 0) {
      c.innerHTML = '<div class="chat-empty">No messages yet. Start a conversation.</div>';
      return;
    }
    msgs.forEach(m => {
      const text = m.content || m.text || '';
      const role = m.role || 'system';
      if (role === 'user') {
        addBubble(esc(text.substring(0, 500)), 'bubble-user');
      } else {
        addBubble(`<span class="dim">${esc(text.substring(0, 500))}</span>`, 'bubble-sys');
      }
    });
  } catch { /* silent — history may be empty */ }
}

/* ==================================================================
   4. DASHBOARD
   ================================================================== */

// Cache observatory data to avoid re-fetching on every tab switch
let _obsCache = null;
let _obsCacheTs = 0;
const OBS_CACHE_TTL = 10000; // 10s

async function fetchObservatory() {
  if (_obsCache && Date.now() - _obsCacheTs < OBS_CACHE_TTL) return _obsCache;
  _obsCache = await api('GET', '/dashboard/observatory');
  _obsCacheTs = Date.now();
  return _obsCache;
}

async function loadDashboard() {
  // Metrics strip — use consolidated observatory endpoint
  try {
    const [obs, health] = await Promise.all([
      fetchObservatory(),
      api('GET', '/health').catch(() => null),
    ]);

    const op = obs.operator || {};
    const grav = obs.gravity || {};
    const mkt = obs.market || {};
    const users = obs.users || {};
    const stIcon = op.status === 'healthy' ? '🟢' : op.status === 'degraded' ? '🟡' : '🔴';

    $('metrics-strip').innerHTML = [
      metric('🌌 Total Stars', obs.total_stars ?? 0),
      metric('⭐ Gravity', grav.total ?? 0),
      metric('📊 Market', mkt.total_signals ?? 0),
      metric('👥 Users', users.total ?? 0),
      metric('💬 Interactions', users.interactions ?? 0),
      metric('🔗 Convergences', (obs.convergences || {}).cross_domain ?? 0),
      metric(`${stIcon} System`, op.status || '—'),
      metric('⏱ Uptime', health ? fmtUptime(health.uptime_seconds) : '—'),
    ].join('');
  } catch (e) {
    $('metrics-strip').innerHTML = `<div class="metric"><span class="dim">Error: ${esc(e.message)}</span></div>`;
  }

  // Load the active section
  const active = document.querySelector('.dash-tab.active');
  const sec = active ? active.dataset.section : 'overview';
  showDashSection(sec);
}

function fmtUptime(s) {
  if (!s) return '—';
  if (s < 3600) return Math.round(s/60) + 'm';
  if (s < 86400) return (s/3600).toFixed(1) + 'h';
  return (s/86400).toFixed(1) + 'd';
}

function metric(label, value) {
  return `<div class="metric"><div class="metric-val">${value}</div><div class="metric-label">${label}</div></div>`;
}

/* ---- Overview (Observatory summary) ---- */
async function loadOverview() {
  const el = $('sec-overview');
  el.innerHTML = '<p class="dim">Cargando observatory…</p>';
  try {
    const obs = await fetchObservatory();
    const grav = obs.gravity || {};
    const legacy = obs.legacy || {};
    const mkt = obs.market || {};
    const users = obs.users || {};
    const op = obs.operator || {};
    const conv = obs.convergences || {};
    const t24 = grav.trends_24h || {};
    const t7 = grav.trends_7d || {};

    let html = `<div class="sys-grid">`;
    // Universe card
    html += `<div class="card">
      <div class="card-head">🌌 Universo</div>
      <div class="kv"><span class="dim">Total estrellas</span><span><strong>${obs.total_stars ?? 0}</strong></span></div>
      <div class="kv"><span class="dim">Gravity engine</span><span>${grav.total ?? 0}</span></div>
      <div class="kv"><span class="dim">Knowledge stars</span><span>${legacy.knowledge_stars ?? 0}</span></div>
      <div class="kv"><span class="dim">User stars</span><span>${legacy.user_stars ?? 0}</span></div>
      <div class="kv"><span class="dim">Patrones</span><span>${legacy.patterns ?? 0}</span></div>
      <div class="kv"><span class="dim">Constelaciones</span><span>${legacy.constellations ?? 0}</span></div>
    </div>`;
    // Trends card
    html += `<div class="card">
      <div class="card-head">📈 Tendencias</div>
      <div class="kv"><span class="dim">Nuevas 24h</span><span>${t24.new ?? 0}</span></div>
      <div class="kv"><span class="dim">Activas 24h</span><span>${t24.active ?? 0}</span></div>
      <div class="kv"><span class="dim">Nuevas 7d</span><span>${t7.new ?? 0}</span></div>
      <div class="kv"><span class="dim">Activas 7d</span><span>${t7.active ?? 0}</span></div>
      <div class="kv"><span class="dim">Creciendo 7d</span><span>${t7.growing ?? 0}</span></div>
    </div>`;
    // Users card
    html += `<div class="card">
      <div class="card-head">👥 Usuarios</div>
      <div class="kv"><span class="dim">Total</span><span>${users.total ?? 0}</span></div>
      <div class="kv"><span class="dim">Interacciones</span><span>${users.interactions ?? 0}</span></div>
      <div class="kv"><span class="dim">Hechos</span><span>${users.facts ?? 0}</span></div>
      <div class="kv"><span class="dim">Core memory</span><span>${users.core_memory ?? 0}</span></div>
      <div class="kv"><span class="dim">Equipos</span><span>${users.teams ?? 0}</span></div>
    </div>`;
    // Market card
    html += `<div class="card">
      <div class="card-head">📊 Mercado</div>
      <div class="kv"><span class="dim">Señales</span><span>${mkt.total_signals ?? 0}</span></div>
      <div class="kv"><span class="dim">Patrones</span><span>${mkt.total_patterns ?? 0}</span></div>
      <div class="kv"><span class="dim">Win rate global</span><span>${mkt.global_win_rate ?? 0}%</span></div>
      <div class="kv"><span class="dim">Convergencias</span><span>${conv.cross_domain ?? 0}</span></div>
    </div>`;
    // Operator card
    html += `<div class="card">
      <div class="card-head">${op.status === 'healthy' ? '🟢' : '🟡'} Operador</div>
      <div class="kv"><span class="dim">Worker</span><span>${op.worker_alive ? '✅ Vivo' : '❌ Muerto'}</span></div>
      <div class="kv"><span class="dim">Cola</span><span>${op.queue_pending ?? 0} pend / ${op.queue_processing ?? 0} proc</span></div>
      <div class="kv"><span class="dim">RAM</span><span>${op.memory_mb ?? '—'} MB</span></div>
      <div class="kv"><span class="dim">Latencia</span><span>${op.avg_latency_s ?? 0}s</span></div>
      <div class="kv"><span class="dim">Audit entries</span><span>${op.audit_entries ?? 0}</span></div>
      <div class="kv"><span class="dim">Ciclos convergencia</span><span>${op.convergence_cycles ?? 0}</span></div>
    </div>`;
    // Domains card
    const domains = grav.domains || {};
    const domainKeys = Object.keys(domains);
    if (domainKeys.length > 0) {
      html += `<div class="card">
        <div class="card-head">🌐 Dominios</div>`;
      domainKeys.forEach(d => {
        html += `<div class="kv"><span class="dim">${esc(d)}</span><span>${domains[d]}</span></div>`;
      });
      html += `</div>`;
    }
    html += `</div>`;
    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Gravity Engine ---- */
async function loadGravity() {
  const el = $('sec-gravity');
  el.innerHTML = '<p class="dim">Cargando gravity engine…</p>';
  try {
    const obs = await fetchObservatory();
    const grav = obs.gravity || {};
    const top = grav.top_stars || [];
    const tiers = grav.tiers || {};

    let html = '';
    // Tier distribution
    const tierKeys = Object.keys(tiers);
    if (tierKeys.length > 0) {
      html += `<div class="card"><div class="card-head">🏗 Distribución por Tier</div>`;
      tierKeys.forEach(t => {
        const cls = t === 'core' ? 'tag-ok' : t === 'consolidated' ? 'tag-warn' : 'tag-dim';
        html += `<div class="kv"><span class="tag ${cls}">${t}</span><span>${tiers[t]}</span></div>`;
      });
      html += `</div>`;
    }
    // Top stars table
    if (top.length > 0) {
      html += `<div class="card"><div class="card-head">⭐ Top Stars (por peso gravitacional)</div>`;
      top.forEach(s => {
        const tierCls = s.tier === 'core' ? 'tag-ok' : s.tier === 'consolidated' ? 'tag-warn' : 'tag-dim';
        html += `<div class="star-row">
          <span class="tag ${tierCls}">${s.tier}</span>
          <span class="dim" style="min-width:70px">${s.domain}</span>
          <span style="min-width:50px">w: <strong>${s.weight}</strong></span>
          <span class="dim">hits:${s.hits} cc:${s.cc} f:${s.freq}</span>
          <span class="dim">${esc(s.summary || s.id)}</span>
        </div>`;
      });
      html += `</div>`;
    } else {
      html += `<p class="dim">Sin estrellas gravitacionales registradas.</p>`;
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Mercado (Market Observatory) ---- */
async function loadMercado() {
  const el = $('sec-mercado');
  el.innerHTML = '<p class="dim">Cargando mercado…</p>';
  try {
    const obs = await fetchObservatory();
    const mkt = obs.market || {};
    const symbols = mkt.symbols || [];

    let html = `<div class="card"><div class="card-head">📊 Market Observatory</div>
      <div class="kv"><span class="dim">Total señales</span><span>${mkt.total_signals ?? 0}</span></div>
      <div class="kv"><span class="dim">Total patrones</span><span>${mkt.total_patterns ?? 0}</span></div>
      <div class="kv"><span class="dim">Win rate global</span><span>${mkt.global_win_rate ?? 0}%</span></div>
    </div>`;

    if (symbols.length > 0) {
      symbols.forEach(s => {
        const wr = s.win_rate || 0;
        const wrCls = wr >= 60 ? 'tag-ok' : wr >= 40 ? 'tag-warn' : 'tag-err';
        const gStar = s.gravity_star;
        html += `<div class="card">
          <div class="card-head">
            <span><strong>${s.symbol}</strong></span>
            <span class="tag ${wrCls}">${wr}% win</span>
            ${gStar ? `<span class="tag tag-dim">tier:${gStar.tier} hits:${gStar.hits}</span>` : ''}
          </div>
          <div class="card-meta">
            <span>${s.signals} señales</span>
            <span>${s.patterns} patrones</span>
            <span>${s.wins}W / ${s.losses}L</span>
            <span>exp: ${s.best_expectancy}</span>
            ${s.last_signal ? `<span class="dim">${s.last_signal.substring(0,16)}</span>` : ''}
          </div>
        </div>`;
      });
    } else {
      html += `<p class="dim">Sin símbolos de mercado en seguimiento.</p>`;
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Convergencias ---- */
async function loadConvergencias() {
  const el = $('sec-convergencias');
  el.innerHTML = '<p class="dim">Cargando convergencias…</p>';
  try {
    const obs = await fetchObservatory();
    const conv = obs.convergences || {};
    const details = conv.details || [];
    const alerts = conv.alert_history || [];

    let html = `<div class="card"><div class="card-head">🔗 Cross-Domain Convergences</div>
      <div class="kv"><span class="dim">Convergencias detectadas</span><span><strong>${conv.cross_domain ?? 0}</strong></span></div>
    </div>`;

    if (details.length > 0) {
      html += `<div class="card"><div class="card-head">Detalle</div>`;
      details.forEach(d => {
        const domains = (d.domains || []).join(', ');
        html += `<div class="kv">
          <span>${esc(d.intent || d.fingerprint || '—')}</span>
          <span class="dim">${domains} — hits:${d.total_hits ?? '?'} cc:${d.avg_cc != null ? d.avg_cc.toFixed(2) : '?'}</span>
        </div>`;
      });
      html += `</div>`;
    }

    if (alerts.length > 0) {
      html += `<div class="card"><div class="card-head">🔔 Historial de Alertas</div>`;
      alerts.forEach(a => {
        html += `<div class="hist-row">
          <span class="dim audit-ts">${(a.timestamp || '').substring(0,16)}</span>
          <span class="tag tag-warn">${esc(a.intent || '—')}</span>
          <span class="dim">${esc(a.message || '')}</span>
        </div>`;
      });
      html += `</div>`;
    } else {
      html += `<p class="dim" style="margin-top:10px">Sin alertas de convergencia recientes.</p>`;
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Stars ---- */
async function loadStars() {
  const el = $('sec-stars');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    // Public endpoint (no auth required)
    const d = await api('GET', '/dashboard/stars?limit=50');
    if (!d.stars || d.stars.length === 0) { el.innerHTML = '<p class="dim">No stars yet.</p>'; return; }
    el.innerHTML = d.stars.map(s => `
      <div class="card">
        <div class="card-head">
          <span class="mono">${s.id.substring(0, 10)}</span>
          <span class="tag tag-${s.layer === 'core' ? 'ok' : s.layer === 'mid' ? 'warn' : 'dim'}">${s.layer}</span>
        </div>
        <p class="card-body">${esc(s.content.substring(0, 200))}</p>
        <div class="card-meta">
          <span>gravity ${s.gravity_score}</span>
          <span>rep ${s.repetition_count}</span>
          <span>${s.channel}/${s.owner}</span>
        </div>
      </div>`).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Constellations ---- */
async function loadConstellations() {
  const el = $('sec-constellations');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/dashboard/constellations?limit=50');
    if (!d.constellations || d.constellations.length === 0) { el.innerHTML = '<p class="dim">No constellations yet.</p>'; return; }
    el.innerHTML = d.constellations.map(c => `
      <div class="card">
        <div class="card-head"><span class="mono">${c.id.substring(0, 10)}</span></div>
        <div class="card-meta">
          <span>${c.member_count} members</span>
          <span>coherence ${c.coherence_score}</span>
          <span>gravity ${c.gravity_score}</span>
          <span>success ${(c.success_rate * 100).toFixed(1)}%</span>
          <span>rep ${c.repetition_count}</span>
        </div>
      </div>`).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- History ---- */
async function loadHistory() {
  const el = $('sec-history');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/dashboard/interactions?limit=50');
    const msgs = d.messages || [];
    if (msgs.length === 0) { el.innerHTML = '<p class="dim">No history yet.</p>'; return; }
    el.innerHTML = msgs.map(m => {
      const role = m.role || 'system';
      const cls  = role === 'user' ? 'tag-warn' : 'tag-ok';
      const name = m.user_name || '';
      return `<div class="hist-row">
        <span class="tag ${cls}">${role}</span>
        <span class="dim" style="min-width:80px">${esc(name)}</span>
        <span class="hist-text">${esc((m.content || '').substring(0, 300))}</span>
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Sessions (Users) ---- */
async function loadSessions() {
  const el = $('sec-sessions');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/dashboard/users');
    const users = d.users || [];
    if (users.length === 0) { el.innerHTML = '<p class="dim">No users yet.</p>'; return; }
    el.innerHTML = users.map(u => {
      const lastDate = u.last_active ? new Date(u.last_active * 1000).toLocaleDateString() : '—';
      return `<div class="card">
        <div class="card-head">
          <span>${esc(u.name)}</span>
          <span class="tag tag-dim">${u.language}</span>
        </div>
        <div class="card-meta">
          <span class="mono">${u.user_id.substring(0, 15)}</span>
          <span>${u.msg_count} messages</span>
          <span>last ${lastDate}</span>
        </div>
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Proposals ---- */
async function loadProposals() {
  const el = $('sec-proposals');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/dashboard/proposals');
    const ps = d.proposals || [];
    if (ps.length === 0) { el.innerHTML = '<p class="dim">Sin propuestas activas. Las propuestas se generan automáticamente cuando el sistema detecta patrones en constelaciones.</p>'; return; }

    const canApprove = USER && (USER.role === 'owner' || USER.role === 'operator');
    el.innerHTML = ps.map(p => {
      const sCls = p.status === 'pending' ? 'tag-warn' : p.status === 'approved' ? 'tag-ok' : 'tag-err';
      const actions = (p.status === 'pending' && canApprove) ? `
        <div class="prop-actions">
          <button class="btn btn-ok btn-sm" onclick="doApprove('${p.id}')">Approve</button>
          <button class="btn btn-err btn-sm" onclick="doReject('${p.id}')">Reject</button>
        </div>` : '';
      return `<div class="card">
        <div class="card-head">
          <span class="mono">${p.id.substring(0, 10)}</span>
          <span class="tag ${sCls}">${p.status}</span>
        </div>
        <p class="card-body">${esc(p.description.substring(0, 300))}</p>
        <div class="card-meta">
          <span>constellation ${p.constellation_id.substring(0, 8)}</span>
          <span>${p.created_at || '—'}</span>
        </div>
        ${p.evidence ? `<p class="card-evidence">${esc(p.evidence.substring(0, 200))}</p>` : ''}
        ${actions}
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

async function doApprove(id) {
  try { await api('POST', `/proposals/${id}/approve`, { reason: 'Approved via UI' }); loadProposals(); }
  catch (e) { alert('Error: ' + e.message); }
}
async function doReject(id) {
  try { await api('POST', `/proposals/${id}/reject`, { reason: 'Rejected via UI' }); loadProposals(); }
  catch (e) { alert('Error: ' + e.message); }
}

/* ---- Operator ---- */
async function loadOperator() {
  const el = $('sec-operator');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const [op, health] = await Promise.all([
      api('GET', '/dashboard/operator'),
      api('GET', '/health').catch(() => null),
    ]);
    const r = op.runtime || {};
    const g = op.governor || {};
    const u = op.universe || {};
    const stIcon = r.status === 'healthy' ? '🟢' : r.status === 'degraded' ? '🟡' : '🔴';
    const wIcon  = r.worker_alive ? '✅' : '❌';
    const govCls = g.mode === 'act' ? 'tag-ok' : g.mode === 'recover' ? 'tag-err' : 'tag-warn';

    let html = `<div class="sys-grid">
      <div class="card">
        <div class="card-head">${stIcon} Runtime</div>
        <div class="kv"><span class="dim">Estado</span><span>${r.status || '—'}</span></div>
        <div class="kv"><span class="dim">Worker</span><span>${wIcon} ${r.worker_alive ? 'vivo' : 'MUERTO'} (${r.worker_heartbeat_age_s ?? '—'}s)</span></div>
        <div class="kv"><span class="dim">Cola</span><span>${r.queue_pending ?? 0} pend / ${r.queue_processing ?? 0} proc / ${r.queue_error ?? 0} err</span></div>
        <div class="kv"><span class="dim">RAM</span><span>${r.memory_mb ?? '—'} MB</span></div>
        <div class="kv"><span class="dim">Latencia</span><span>avg ${r.avg_latency_s ?? 0}s / max ${r.max_latency_s ?? 0}s</span></div>
        <div class="kv"><span class="dim">Usuarios activos</span><span>${r.active_users ?? 0}</span></div>
      </div>
      <div class="card">
        <div class="card-head">⚖️ Governor</div>
        <div class="kv"><span class="dim">Modo</span><span class="tag ${govCls}">${g.mode || '—'}</span></div>
        <div class="kv"><span class="dim">Razón</span><span>${esc(g.reason || '—')}</span></div>
        <div class="kv"><span class="dim">Autopatch</span><span>${g.autopatch_allowed ? 'Sí' : 'No'}</span></div>
        <div class="kv"><span class="dim">Racha limpia</span><span>${g.clean_streak ?? 0}</span></div>
      </div>
      <div class="card">
        <div class="card-head">🌌 Universo</div>
        <div class="kv"><span class="dim">Knowledge Stars</span><span>${u.knowledge_stars ?? 0}</span></div>
        <div class="kv"><span class="dim">User Stars</span><span>${u.user_stars ?? 0}</span></div>
        <div class="kv"><span class="dim">Masa total</span><span>${u.total_mass ?? 0}</span></div>
        <div class="kv"><span class="dim">Patrones</span><span>${u.pattern_count ?? 0}</span></div>
        <div class="kv"><span class="dim">Convergencias</span><span>${u.convergences ?? 0}</span></div>
        <div class="kv"><span class="dim">Memoria profunda</span><span>${u.deep_memory ?? 0}</span></div>
        <div class="kv"><span class="dim">Errores 24h</span><span>${u.errors_24h ?? 0}</span></div>
      </div>
    </div>`;

    // Layers
    if (op.layers && op.layers.length > 0) {
      html += '<div class="card" style="margin-top:12px"><div class="card-head">Operator Layers</div>';
      html += op.layers.map(l => {
        const cls = l.status === 'active' ? 'tag-ok' : 'tag-warn';
        return `<div class="layer-row"><span class="layer-id">${l.id}</span> <span>${esc(l.name)}</span> <span class="tag ${cls}">${l.status}</span></div>`;
      }).join('');
      html += '</div>';
    }

    // Health
    if (health) {
      html += `<div class="card" style="margin-top:12px">
        <div class="card-head">Health</div>
        <div class="kv"><span class="dim">Status</span><span class="tag tag-ok">${health.status}</span></div>
        <div class="kv"><span class="dim">Uptime</span><span>${Math.round(health.uptime_seconds)}s</span></div>
      </div>`;
    }

    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Audit ---- */
async function loadAudit() {
  const el = $('sec-audit');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/dashboard/audit?limit=50');
    const entries = d.entries || [];
    if (entries.length === 0) { el.innerHTML = '<p class="dim">Sin eventos de auditoría registrados.</p>'; return; }
    el.innerHTML = entries.map(e => {
      const cls = e.decision === 'approved' ? 'tag-ok' : e.decision === 'rejected' ? 'tag-err' : 'tag-warn';
      return `<div class="audit-row">
        <span class="dim audit-ts">${e.timestamp || ''}</span>
        <span class="tag ${cls}">${e.decision || e.action || '—'}</span>
        <span class="dim">${esc(e.actor || '')} ${e.role ? '(' + e.role + ')' : ''}</span>
        <span>${esc(e.action || '')}</span>
        <span class="dim">${esc(e.reason || '')}</span>
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}

/* ---- Sistema (monitor operacional) ---- */
async function loadSistema() {
  const el = $('sec-sistema');
  el.innerHTML = '<p class="dim">Cargando estado del sistema…</p>';
  clearInterval(_sistemaTimer);
  _sistemaTimer = setInterval(() => {
    if (document.querySelector('#sec-sistema.active')) _renderSistema();
  }, 15000);
  await _renderSistema();
}

async function _renderSistema() {
  const el = $('sec-sistema');
  try {
    const d  = await api('GET', '/system/monitor');
    const r  = d.runtime   || {};
    const g  = d.governor  || {};
    const ml = d.meta_loop || {};
    const id = d.ideas     || {};
    const ts = d.sampled_at ? new Date(d.sampled_at * 1000).toLocaleTimeString() : '—';
    const stIcon = r.status === 'healthy' ? '🟢' : r.status === 'degraded' ? '🟡' : '🔴';
    const wIcon  = r.worker_alive ? '✅' : '❌';
    const govCls = g.mode === 'act' ? 'tag-ok' : g.mode === 'recover' ? 'tag-err' : 'tag-warn';
    el.innerHTML = `
      <div class="sys-header dim">Actualizado: ${ts} — auto-refresh 15s</div>
      <div class="sys-grid">
        <div class="card">
          <div class="card-head">${stIcon} Runtime</div>
          <div class="kv"><span class="dim">Estado</span><span>${r.status || '—'}</span></div>
          <div class="kv"><span class="dim">Worker</span><span>${wIcon} ${r.worker_alive ? 'vivo' : 'MUERTO'} (${r.worker_heartbeat_age_s ?? '—'}s)</span></div>
          <div class="kv"><span class="dim">Cola</span><span>${r.queue_pending ?? 0} pend / ${r.queue_processing ?? 0} proc</span></div>
          <div class="kv"><span class="dim">RAM</span><span>${r.memory_mb ?? '—'} MB</span></div>
          <div class="kv"><span class="dim">Usuarios activos</span><span>${r.active_users ?? 0}</span></div>
        </div>
        <div class="card">
          <div class="card-head">⚖️ Governor</div>
          <div class="kv"><span class="dim">Modo</span><span class="tag ${govCls}">${g.mode || '—'}</span></div>
          <div class="kv"><span class="dim">Razón</span><span>${esc(g.reason || '—')}</span></div>
          <div class="kv"><span class="dim">Autopatch</span><span>${g.autopatch_allowed ? 'Sí' : 'No'}</span></div>
          <div class="kv"><span class="dim">Racha limpia</span><span>${g.clean_streak ?? 0}</span></div>
        </div>
        <div class="card">
          <div class="card-head">🔄 Meta-Loop</div>
          <div class="kv"><span class="dim">Actividad</span><span>${esc(ml.activity || '—')}</span></div>
          <div class="kv"><span class="dim">Salud</span><span>${esc(ml.health || '—')}</span></div>
          <div class="kv"><span class="dim">Ciclos</span><span>${ml.cycles ?? '—'}</span></div>
          <div class="kv"><span class="dim">Uptime</span><span>${esc(ml.uptime || '—')}</span></div>
          ${ml.idea_alerts_sent != null ? `<div class="kv"><span class="dim">Alertas ideas</span><span>${ml.idea_alerts_sent}</span></div>` : ''}
          <div class="kv"><span class="dim">Última reflexión</span><span class="dim">${esc((ml.timestamp || '').substring(0,19))}</span></div>
        </div>
        <div class="card">
          <div class="card-head">🧬 Ideas</div>
          <div class="kv"><span class="dim">Total</span><span>${id.total ?? 0}</span></div>
          <div class="kv"><span class="dim">Pendientes</span><span>${(id.by_status || {}).pending ?? 0}</span></div>
          <div class="kv"><span class="dim">Aprobadas</span><span>${(id.by_status || {}).approved ?? 0}</span></div>
          <div class="kv"><span class="dim">Convergencia</span><span>${id.convergence != null ? (id.convergence * 100).toFixed(1)+'%' : '—'}</span></div>
          ${(id.top3 || []).map(i => {
            const pc = i.priority==='critical'?'tag-err':i.priority==='high'?'tag-warn':'tag-ok';
            return `<div class="kv"><span class="tag ${pc}">${i.priority}</span><span class="dim">${esc(i.title.substring(0,35))}</span></div>`;
          }).join('')}
        </div>
      </div>`;
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Ideas (IdeaStore) ---- */
async function loadIdeas() {
  const el = $('sec-ideas');
  el.innerHTML = '<p class="dim">Cargando ideas…</p>';
  try {
    const d     = await api('GET', '/ideas?limit=50');
    const ideas = d.ideas || [];
    const stats = d.stats  || {};
    const conv  = stats.convergence_level != null ? (stats.convergence_level*100).toFixed(1)+'%' : '—';
    const canWrite = USER && (USER.role === 'owner' || USER.role === 'operator' || USER.role === 'creator');
    const header = `
      <div class="ideas-header">
        <div class="ideas-stats">
          <span>Total: <strong>${stats.total??0}</strong></span>
          <span>Pendientes: <strong>${(stats.by_status||{}).pending??0}</strong></span>
          <span>Aprobadas: <strong>${(stats.by_status||{}).approved??0}</strong></span>
          <span>Convergencia: <strong>${conv}</strong></span>
        </div>
        ${canWrite ? '<button class="btn btn-sm btn-accent" onclick="refreshIdeas()">↻ Actualizar</button>' : ''}
      </div>`;
    if (ideas.length === 0) { el.innerHTML = header + '<p class="dim">Sin ideas. El núcleo está estable.</p>'; return; }
    const pIcons = {critical:'🔴', high:'🟠', medium:'🟡', low:'⚪'};
    const sCls   = {pending:'tag-warn', approved:'tag-ok', rejected:'tag-err', applied:'tag-dim'};
    el.innerHTML = header + ideas.map(i => {
      const actions = (i.status==='pending' && canWrite) ? `
        <div class="prop-actions">
          <button class="btn btn-ok btn-sm" onclick="doApproveIdea('${i.idea_id}')">&#10003; Aprobar</button>
          <button class="btn btn-err btn-sm" onclick="doRejectIdea('${i.idea_id}')">&#10005; Rechazar</button>
        </div>` : '';
      return `<div class="card">
        <div class="card-head">
          <span>${pIcons[i.priority]||'⚫'} <span class="mono">${i.idea_id}</span></span>
          <span class="tag ${sCls[i.status]||'tag-warn'}">${i.status}</span>
        </div>
        <p class="card-body">${esc(i.title)}</p>
        <p class="card-body dim" style="font-size:12px">${esc((i.description||'').substring(0,200))}</p>
        <div class="card-meta">
          <span>${i.priority.toUpperCase()}</span>
          <span>score ${i.priority_score}</span>
          <span>${esc(i.affected_component)}</span>
          <span>${esc(i.source)}</span>
          <span class="dim">${(i.created_at||'').substring(0,16)}</span>
        </div>
        ${actions}
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

async function refreshIdeas() {
  try {
    const d = await api('POST', '/ideas/refresh');
    if (d.total_new > 0) alert(`+${d.total_new} ideas importadas.`);
    loadIdeas();
  } catch (e) { alert('Error: ' + e.message); }
}
async function doApproveIdea(id) {
  try { await api('POST', `/ideas/${id}/approve`, {}); loadIdeas(); }
  catch (e) { alert('Error: ' + e.message); }
}
async function doRejectIdea(id) {
  try { await api('POST', `/ideas/${id}/reject`, {}); loadIdeas(); }
  catch (e) { alert('Error: ' + e.message); }
}

/* ==================================================================
   EVENT WIRING
   ================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  // Login
  $('btn-login').addEventListener('click', doLogin);
  $('login-user').addEventListener('keydown', e => { if (e.key === 'Enter') $('login-pass').focus(); });
  $('login-pass').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  // Logout
  $('btn-logout').addEventListener('click', doLogout);

  // Chat
  $('btn-send').addEventListener('click', sendChat);
  $('chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });

  // Top nav
  document.querySelectorAll('.nav-btn').forEach(b =>
    b.addEventListener('click', () => showPanel(b.dataset.view))
  );

  // Dashboard sub-tabs
  document.querySelectorAll('.dash-tab').forEach(b =>
    b.addEventListener('click', () => showDashSection(b.dataset.section))
  );

  // Session restore
  if (TOKEN && USER) {
    api('GET', '/auth/me')
      .then(() => enterApp())
      .catch(() => doLogout());
  }
});
