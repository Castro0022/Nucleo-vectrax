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
  const loaders = { stars: loadStars, constellations: loadConstellations,
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

async function loadDashboard() {
  // Metrics strip
  try {
    const [stats, status, health] = await Promise.all([
      api('GET', '/memory/stats'),
      api('GET', '/status').catch(() => null),
      api('GET', '/health').catch(() => null),
    ]);

    const gov = status ? status.governor_mode : '?';
    const govCls = gov === 'act' ? 'tag-ok' : gov === 'recover' ? 'tag-err' : 'tag-warn';

    $('metrics-strip').innerHTML = [
      metric('Stars', stats.stars),
      metric('Constellations', stats.constellations),
      metric('Pending', stats.pending_proposals),
      metric('Governor', `<span class="tag ${govCls}">${gov}</span>`),
      metric('Graph', status ? `${status.graph_nodes}n / ${status.graph_edges}e` : '—'),
      metric('Uptime', health ? `${Math.round(health.uptime_seconds)}s` : '—'),
    ].join('');
  } catch (e) {
    $('metrics-strip').innerHTML = `<div class="metric"><span class="dim">Error: ${esc(e.message)}</span></div>`;
  }

  // Load the active section
  const active = document.querySelector('.dash-tab.active');
  const sec = active ? active.dataset.section : 'stars';
  showDashSection(sec);
}

function metric(label, value) {
  return `<div class="metric"><div class="metric-val">${value}</div><div class="metric-label">${label}</div></div>`;
}

/* ---- Stars ---- */
async function loadStars() {
  const el = $('sec-stars');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/memory/stars?limit=50');
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
    const d = await api('GET', '/memory/constellations?limit=50');
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
    const d = await api('GET', '/memory/history?limit=50');
    const msgs = d.messages || [];
    if (msgs.length === 0) { el.innerHTML = '<p class="dim">No history yet.</p>'; return; }
    el.innerHTML = msgs.map(m => {
      const role = m.role || 'system';
      const cls  = role === 'user' ? 'tag-warn' : 'tag-ok';
      return `<div class="hist-row">
        <span class="tag ${cls}">${role}</span>
        <span class="hist-text">${esc((m.content || m.text || '').substring(0, 300))}</span>
        <span class="dim hist-meta">${m.session_id ? m.session_id.substring(0, 8) + '…' : ''}</span>
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Sessions ---- */
async function loadSessions() {
  const el = $('sec-sessions');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/memory/sessions');
    const s = d.sessions || [];
    if (s.length === 0) { el.innerHTML = '<p class="dim">No sessions yet.</p>'; return; }
    el.innerHTML = s.map(x => `
      <div class="card">
        <div class="card-head"><span class="mono">${(x.session_id || x.id || '').substring(0, 14)}</span></div>
        <div class="card-meta">
          <span>${x.message_count || x.count || 0} messages</span>
          <span>started ${x.started_at || x.first_ts || '—'}</span>
          <span>last ${x.last_at || x.last_ts || '—'}</span>
        </div>
      </div>`).join('');
  } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- Proposals ---- */
async function loadProposals() {
  const el = $('sec-proposals');
  el.innerHTML = '<p class="dim">Loading…</p>';
  try {
    const d = await api('GET', '/proposals');
    const ps = d.proposals || [];
    if (ps.length === 0) { el.innerHTML = '<p class="dim">No proposals.</p>'; return; }

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
    const [op, status, health] = await Promise.all([
      api('GET', '/status/operator'),
      api('GET', '/status').catch(() => null),
      api('GET', '/health').catch(() => null),
    ]);

    let html = `<div class="card">
      <div class="card-head"><strong>${esc(op.operator_name)}</strong> <span class="dim">${op.mode} mode</span></div>
      <div class="card-meta"><span>${op.active_layers}/${op.total_layers} layers active</span></div>
    </div>`;

    // Layers
    html += (op.layers || []).map(l => {
      const cls = l.status === 'active' ? 'tag-ok' : 'tag-warn';
      return `<div class="layer-row"><span class="layer-id">${l.id}</span> <span>${esc(l.name)}</span> <span class="tag ${cls}">${l.status}</span></div>`;
    }).join('');

    // System status
    if (status) {
      const govCls = status.governor_mode === 'act' ? 'tag-ok' : status.governor_mode === 'recover' ? 'tag-err' : 'tag-warn';
      html += `<div class="card" style="margin-top:12px">
        <div class="card-head">System Status</div>
        <div class="kv"><span class="dim">Governor</span><span class="tag ${govCls}">${status.governor_mode}</span></div>
        <div class="kv"><span class="dim">Reason</span><span>${esc(status.governor_reason)}</span></div>
        <div class="kv"><span class="dim">Autopatch</span><span>${status.autopatch_allowed ? 'Yes' : 'No'}</span></div>
        <div class="kv"><span class="dim">Clean streak</span><span>${status.clean_streak}</span></div>
        <div class="kv"><span class="dim">Graph</span><span>${status.graph_nodes} nodes / ${status.graph_edges} edges</span></div>
      </div>`;
    }

    // Health
    if (health) {
      const compHtml = Object.entries(health.components || {}).map(([k, v]) => {
        const c = (v === 'ok' || v === 'act') ? 'tag-ok' : 'tag-warn';
        return `<div class="kv"><span class="dim">${k}</span><span class="tag ${c}">${v}</span></div>`;
      }).join('');
      html += `<div class="card" style="margin-top:12px">
        <div class="card-head">Health</div>
        <div class="kv"><span class="dim">Status</span><span class="tag tag-ok">${health.status}</span></div>
        <div class="kv"><span class="dim">Version</span><span>${health.version}</span></div>
        <div class="kv"><span class="dim">Env</span><span>${health.env}</span></div>
        <div class="kv"><span class="dim">Uptime</span><span>${Math.round(health.uptime_seconds)}s</span></div>
        ${compHtml}
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
    const d = await api('GET', '/status/audit?limit=30');
    const entries = d.entries || [];
    if (entries.length === 0) { el.innerHTML = '<p class="dim">No audit entries.</p>'; return; }
    el.innerHTML = entries.map(e => {
      const cls = e.decision === 'approved' ? 'tag-ok' : e.decision === 'rejected' ? 'tag-err' : 'tag-warn';
      return `<div class="audit-row">
        <span class="dim audit-ts">${e.timestamp || ''}</span>
        <span class="tag ${cls}">${e.decision || e.action}</span>
        <span class="dim">${esc(e.actor)} (${e.role})</span>
        <span>${esc(e.action)}</span>
        <span class="dim">${esc(e.reason || '')}</span>
      </div>`;
    }).join('');
  } catch {
    el.innerHTML = '<p class="dim">Audit not available (requires audit.read permission)</p>';
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
