'use strict';
/* Close Commenter front end. Vanilla JS, one global state object S, hash routing.
   Every dynamic string goes through esc(). The server enforces everything; this file only shows it. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const usd = c => (c < 0 ? '-' : '') + '$' + (Math.abs(c) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = n => Number(n).toLocaleString('en-US');
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const shortDate = d => (d && d.length >= 10) ? MON[+d.slice(5, 7) - 1] + ' ' + d.slice(8, 10) : esc(d);
const when = ts => ts ? esc(ts.replace('T', ' ').slice(0, 16)) : '';
const words = t => (t.match(/\w+/g) || []).length;   // same rule as the server: tokens with a letter or digit
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const REASONS = { second_opinion: 'Second opinion', investigation: 'Investigation', clarification: 'Clarification', approval: 'Approval', refund_request: 'Refund request' };
const STATE = { pending: 'Pending', routed: 'Routed', approved: 'Approved', cancelled: 'Cancelled', refund_requested: 'Approved, refund requested' };
/* approval reasons: [chip label, note prefill]. The server stores the code as the resolution. */
const RES = { supported: ['Supported', () => 'Supported by invoice and contract'], prepayment: ['Prepayment', m => `Prepayment covering ${m} months`], one_time: ['One-time', () => 'One-time item, not recurring'], timing: ['Timing', () => 'Timing: belongs to another period'] };
const resWords = r => !r || !r.code ? '' : r.code === 'prepayment' ? `Prepayment, ${num(r.months)} months` : r.code === 'refund_request' ? `Refund requested, ${usd(r.recover_cents)} to recover` : ({ supported: 'Supported', one_time: 'One-time item', timing: 'Timing, another period' })[r.code] || String(r.code);
const AUD = { cancel: 'cancelled entry', refund_request: 'refund requested', reopen: 'reopened' };
const audName = a => AUD[a] || String(a).replace(/_/g, ' ');
const TABS = [['cockpit', 'Cockpit'], ['review', 'Review'], ['inbox', 'Inbox'], ['insights', 'Insights'], ['deck', 'Deck'], ['anchor', 'Anchor'], ['audit', 'Audit']];
const CHEAT_ID = '2609-0217';
const TIP = 'Write at least 25 words in your own words first. Enhance polishes your text; it never writes for you.';

const S = {
  me: null, users: [], tab: 'cockpit', retry: null,
  txns: [], total: 0, sel: null, detail: {}, notes: {}, filters: { kind: '', status: '', risk: '', q: '' },
  batch: null, batchDone: null, inboxN: 0,
  ins: null, cm: {}, charts: [], regen: {}, fin: false, prefill: '',
  status: null, verify: null, restore: null, tamper: null, blockers: null, busy: '',
  loginUser: null,
};
if (/theme=dark/.test(location.search)) document.documentElement.dataset.theme = 'dark';

/* ---------- api ---------- */
async function api(method, path, body) {
  const r = await fetch(path, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await r.json(); } catch (_) { /* non-JSON error body */ }
  if (!r.ok) {
    const e = new Error((data && data.error) || r.statusText || 'Request failed');
    e.status = r.status; e.data = data || {};
    throw e;
  }
  return data;
}
const get = p => api('GET', p);
const post = (p, b) => api('POST', p, b || {});

function fail(e, retry) {
  if (e.status === 401) { if (S.me) { S.me = null; route(); } return; }
  S.retry = retry || route;
  const m = $('#main') || $('#app');
  m.innerHTML = `<div class="banner bad" role="alert"><span>${esc(e.message || 'Request failed')}</span><button class="btn" data-act="retry">Retry</button></div>`;
}
async function busy(btn, label, fn) {
  if (!btn || btn.disabled) return;
  const old = btn.textContent;
  btn.disabled = true; if (label) btn.textContent = label;
  try { return await fn(); } finally { if (btn.isConnected) { btn.disabled = false; if (label) btn.textContent = old; } }
}
const rsn = a => (a || []).map(x => String(x).replace(/[.;\s]+$/, '')).filter(Boolean).join('; ') + '.';
const uname = id => (S.users.find(u => u.id === id) || {}).name || id || '';
const can = () => S.me && ['controller', 'cfo'].includes(S.me.user.role);
const canWrite = () => S.me && ['controller', 'cfo', 'preparer'].includes(S.me.user.role);
const stOf = t => t.state || t.status;
const decided = t => t.status === 'approved' || t.status === 'cancelled';
const cashExp = t => t.kind === 'activity' && !/Revenue/.test(t.category);   // the server checks its own category table

/* ---------- boot + routing ---------- */
async function boot() {
  try { S.users = await get('/api/users'); } catch (_) { S.users = []; }
  /* Sign out also sets a per-tab flag, so a reload stays signed out even if the server keeps the session cookie. */
  let out = false; try { out = sessionStorage.getItem('cc_out') === '1'; } catch (_) { /* storage blocked */ }
  if (!out) { try { S.me = await get('/api/me'); } catch (_) { S.me = null; } }
  route();
}
const VIEWS = { cockpit, review, inbox, insights, deck: () => deckView(), anchor, audit };
async function route() {
  destroyCharts();
  if (typeof cardsUnmount === 'function') cardsUnmount();
  if (typeof deckUnmount === 'function') deckUnmount();
  if (!S.me) return login();
  const name = location.hash.replace(/^#\//, '').split('?')[0] || 'cockpit';
  S.tab = VIEWS[name] ? name : 'cockpit';
  shell();
  get('/api/inbox').then(r => { S.inboxN = r.open.length; const el = $('#inboxN'); if (el) el.textContent = S.inboxN || ''; }).catch(() => {});
  try { await VIEWS[S.tab](); } catch (e) { fail(e, route); }
}
addEventListener('hashchange', route);

function shell() {
  const m = S.me, ai = m.ai || {}, off = ai.system2 === 'off', noJev = ai.jev === false;
  $('#app').innerHTML = `
  <header class="bar">
    <strong class="brand">${esc(m.company)}</strong>
    <span class="muted">Period ${esc(m.period)}</span>
    ${off ? '<span class="chip warn" title="System 2 (Claude) is off. Narratives use code templates and Enhance falls back to the recorded example.">AI offline</span>' : ''}
    ${noJev ? '<span class="chip warn" title="System 1 (JEV) is not configured. Items show as not screened and the batch gate ignores JEV.">Screen offline</span>' : ''}
    <span class="grow"></span>
    <span class="who">${esc(m.user.name)}<span class="muted">, ${esc(m.user.role)}</span></span>
    <details class="demo" id="demoMenu">
      <summary class="btn" aria-label="Demo controls">Demo</summary>
      <div class="demo-pop">
        <button class="btn" id="resetBtn" data-act="reset">Reset demo data</button>
        <p class="small muted">Restores the seeded September close.</p>
        <hr>
        <button class="btn" id="shortcutBtn" data-act="shortcut" ${can() ? '' : 'disabled'}>Demo shortcut: approve the rest</button>
        <p class="small muted">Labelled shortcut: approves everything still open, writes an audit entry.${can() ? '' : ' Controller or CFO only.'}</p>
      </div>
    </details>
    <button class="btn" data-act="logout">Sign out</button>
  </header>
  <nav class="tabs" aria-label="Sections">${TABS.map(([k, l]) =>
    `<a class="tab" href="#/${k}"${S.tab === k ? ' aria-current="page"' : ''}>${l}${k === 'inbox' ? `<span class="count" id="inboxN">${S.inboxN || ''}</span>` : ''}</a>`).join('')}</nav>
  <div id="flash" class="flash" role="status" hidden></div>
  <main id="main" class="v-${S.tab}" tabindex="-1"></main>`;
}
function flash(msg, cls = 'ok') {
  const f = $('#flash'); if (!f) return;
  f.className = 'flash ' + cls; f.innerHTML = msg; f.hidden = false;
  clearTimeout(flash.t); flash.t = setTimeout(() => { f.hidden = true; }, 6000);
}

/* ---------- login ---------- */
async function login(err) {
  if (!S.users.length) { try { S.users = await get('/api/users'); } catch (e) { err = e.message; } }
  const sel = S.users.find(u => u.id === S.loginUser) || null;
  $('#app').innerHTML = `
  <div class="login">
    <section class="login-l">
      <h1>Close Commenter</h1>
      <p class="lede">Month-end close for Northgate Labs, September 2026.</p>
      <ul class="guar">
        <li><strong>Code computes.</strong> Every number, flag and total comes from code you can read. AI never supplies a figure.</li>
        <li><strong>You approve.</strong> A fast AI screen and a writing assistant help, but only a person approves, and the server checks every approval again.</li>
        <li><strong>The chain remembers.</strong> The approved close is hashed and anchored on the Hedera testnet, so anyone can verify it later, even if QuickBooks is gone.</li>
      </ul>
      <div class="banner warn"><span>Demo auth, synthetic data, not real security.</span></div>
    </section>
    <section class="login-r">
      <h2>Sign in as</h2>
      <div class="personas" role="group" aria-label="Demo users">${S.users.map(u => `
        <button type="button" class="persona${sel && sel.id === u.id ? ' on' : ''}" data-act="persona" data-id="${esc(u.id)}" aria-pressed="${sel && sel.id === u.id}">
          <span class="p-name">${esc(u.name)}</span><span class="chip neutral">${esc(u.role)}</span><span class="p-hint muted">${esc(u.hint)}</span>
        </button>`).join('')}</div>
      <form id="loginForm" autocomplete="off">
        <label>Password <span class="muted">(demo password, shown on purpose)</span>
          <input id="lpw" type="text" value="${sel ? esc(sel.demo_password) : ''}" ${sel ? '' : 'disabled'} spellcheck="false"></label>
        <div id="loginErr" class="box bad" role="alert" ${err ? '' : 'hidden'}>${esc(err || '')}</div>
        <button class="btn primary" type="submit" ${sel ? '' : 'disabled'}>Sign in${sel ? ' as ' + esc(sel.name) : ''}</button>
      </form>
    </section>
  </div>`;
}
document.addEventListener('submit', async e => {
  if (e.target.id === 'loginForm') {
    e.preventDefault();
    const btn = $('button[type=submit]', e.target);
    await busy(btn, 'Signing in', async () => {
      try {
        await post('/api/login', { user_id: S.loginUser, password: $('#lpw').value });
        try { sessionStorage.removeItem('cc_out'); } catch (_) { /* storage blocked */ }
        S.me = await get('/api/me');
        S.users = await get('/api/users').catch(() => S.users);
        if (location.hash === '#/cockpit') route(); else location.hash = '#/cockpit';
      } catch (er) { const b = $('#loginErr'); b.hidden = false; b.textContent = er.message; }
    });
  } else if (e.target.id === 'routeForm') {
    e.preventDefault();
    await submitRoute();
  }
});

/* ---------- cockpit ---------- */
const firstFlag = t => (t.flags[0] || {}).msg || '';
async function cockpit() {
  const [s, top] = await Promise.all([get('/api/summary'), get('/api/txns?status=pending&risk=high&limit=5').catch(() => ({ rows: [], total: 0 }))]);
  const C = 2 * Math.PI * 84, t = Math.max(s.total, 1);
  const dec = s.decided ?? s.approved, nc = s.cancelled || 0, na = s.actions_open || 0;
  S.fin = s.finalized;
  const parts = [['appr', dec, 0], ['routed', s.routed, dec]]; /* decided = approved + cancelled; pending is the track */
  const done = s.pending === 0 && s.routed === 0;
  const hint = s.finalized ? 'The close is finalized. Verify it against the chain on the Anchor tab.'
    : s.flagged_pending > 0 ? `${s.flagged_pending} flagged item${s.flagged_pending === 1 ? '' : 's'} still pending. Read those one by one in Review, then use Check eligible for the rest.`
    : s.pending > 0 ? 'No flagged items are waiting. Run Check eligible in Review to approve the rest in one batch.'
    : s.routed > 0 ? `${s.routed} routed item${s.routed === 1 ? '' : 's'} wait for a reply. Open the Inbox.`
    : `Everything is ${nc ? 'approved or cancelled' : 'approved'}.${na ? ` ${na} refund request${na === 1 ? ' stays' : 's stay'} open with the Payments desk and ${na === 1 ? 'does' : 'do'} not block the close.` : ''} Pin one insight with 25 or more words of your own, then finalize on the Anchor tab.`;
  const cta = s.finalized || done ? ['#/anchor', 'Open Anchor'] : s.pending === 0 && s.routed > 0 ? ['#/inbox', 'Open Inbox'] : ['#/review', 'Open Review'];
  const tile = (cls, label, val, sub, hero) => `<div class="tile${hero ? ' hero' + (hero === 'bad' ? ' bad' : '') : ''}"><dt>${cls ? `<i class="sw ${cls}"></i>` : ''}${label}</dt><dd>${val}</dd>${sub ? `<span class="sub">${sub}</span>` : ''}</div>`;
  $('#main').innerHTML = `
  <h1>Close readiness</h1>
  <div class="cockpit">
    <section class="sheet ring-wrap" aria-label="Readiness">
      <svg class="ring" viewBox="0 0 200 200" role="img" aria-label="${s.approved} approved, ${nc} cancelled, ${s.routed} routed, ${s.pending} pending of ${s.total}">
        <circle class="trk" cx="100" cy="100" r="84"/>
        ${parts.map(([k, n, start]) => `<circle class="arc ${k}" cx="100" cy="100" r="84" transform="rotate(-90 100 100)" stroke-dasharray="0 ${C}" stroke-dashoffset="${-start / t * C}" data-len="${n / t * C}" data-c="${C}"/>`).join('')}
      </svg>
      <div class="ring-n"><span class="big">${s.readiness_pct}%</span><span class="muted">${nc ? 'decided' : 'approved'}</span></div>
    </section>
    <section class="sheet cockpit-r">
      <div class="ch"><h2>${esc(s.period)} close for ${esc(S.me.company)}</h2><span class="muted">${num(s.total)} transactions: ${num(s.by_kind.activity)} activity, ${num(s.by_kind.accounting)} accounting-only</span></div>
      <dl class="tiles">
        ${tile('appr', 'Approved', num(s.approved), `of ${num(s.total)}`)}
        ${tile('', 'Cancelled', num(nc), 'accounting-only entries')}
        ${tile('routed', 'Routed', num(s.routed), 'waiting for a reply')}
        ${tile('pend', 'Pending', num(s.pending), 'not yet reviewed')}
        ${tile('', 'Flagged by code', num(s.flagged), `${num(s.flagged_pending)} still pending`)}
        ${tile('', 'At risk', usd(s.at_risk_cents), 'flagged, not yet decided', s.at_risk_cents > 0 ? 'bad' : true)}
        ${tile('', 'Review adjustments', usd(s.adjustment_cents || 0), 'cancelled, refunds, prepayments')}
        ${tile('', 'Action items open', num(na), 'refunds with the Payments desk')}
      </dl>
      <div class="next"><p class="hint"><strong>Next:</strong> ${esc(hint)}</p><a class="btn primary" href="${cta[0]}">${cta[1]}</a></div>
    </section>
    <section class="sheet first" aria-label="Review first">
      <div class="ch"><h2>Review first</h2><span class="muted">Highest risk, still pending. Click a row to open it.</span></div>
      ${top.rows.length ? `<table class="grid small first-t"><colgroup><col style="width:92px"><col style="width:230px"><col style="width:110px"><col style="width:84px"><col></colgroup><tbody>${top.rows.map(r => `<tr data-act="top" data-id="${esc(r.id)}"><td><a class="mono" href="#/review" data-act="top" data-id="${esc(r.id)}">${esc(r.id)}</a></td><td class="ell" title="${esc(r.vendor)}">${esc(r.vendor)}</td><td class="r">${usd(r.amount_cents)}</td><td>${riskChip(r)}</td><td class="ell muted" title="${esc(firstFlag(r))}">${esc(firstFlag(r))}</td></tr>`).join('')}</tbody></table>`
        : `<p class="empty">${s.finalized ? 'Nothing left to review.' : 'No high-risk items are waiting.'}</p>`}
    </section>
  </div>`;
  const el = $('.big'), n = s.readiness_pct, t0 = performance.now(); /* count the number up with the arc */
  const step = ts => { const k = Math.max(0, Math.min(1, (ts - t0) / 600)); el.textContent = Math.round(n * (1 - Math.pow(1 - k, 3))) + '%'; if (k < 1) requestAnimationFrame(step); };
  if (!reduced && n > 0) { el.textContent = '0%'; requestAnimationFrame(step); }
  const go = () => $$('.arc').forEach(a => a.setAttribute('stroke-dasharray', `${a.dataset.len} ${a.dataset.c}`));
  reduced ? go() : requestAnimationFrame(() => requestAnimationFrame(go));
}

/* ---------- review ---------- */
const rvSeg = () => `<div class="seg" role="group" aria-label="Review view">${[['table', 'Table'], ['cards', 'Cards']].map(([v, l]) => `<button type="button" data-act="rvmode" data-v="${v}" aria-pressed="${(S.rvMode || 'table') === v}">${l}</button>`).join('')}</div>`;
async function review() {
  const f = S.filters;
  if (S.rvMode === 'cards') { /* second review view: static/cards.js owns everything inside #cardsRoot */
    $('#main').innerHTML = `<div class="rv-cards"><div class="toolbar">${rvSeg()}<span class="muted">One item at a time. The server still decides.</span></div><div id="cardsRoot"></div></div>`;
    return cardsMount($('#cardsRoot'));
  }
  $('#main').innerHTML = `
  <div class="rv">
    <div class="rv-main">
      <div class="toolbar">
        ${rvSeg()}
        <div class="seg" role="group" aria-label="Kind">${[['', 'All'], ['activity', 'Activity'], ['accounting', 'Accounting-only']].map(([v, l]) =>
          `<button type="button" data-act="kind" data-v="${v}" aria-pressed="${f.kind === v}">${l}</button>`).join('')}</div>
        <select id="fStatus" aria-label="Status">${[['', 'All status'], ['pending', 'Pending'], ['approved', 'Approved'], ['refund_requested', 'Refund requested'], ['cancelled', 'Cancelled'], ['routed', 'Routed']].map(([v, l]) => `<option value="${v}"${f.status === v ? ' selected' : ''}>${l}</option>`).join('')}</select>
        <select id="fRisk" aria-label="Risk">${[['', 'All risk'], ['high', 'High'], ['med', 'Medium'], ['low', 'Low']].map(([v, l]) => `<option value="${v}"${f.risk === v ? ' selected' : ''}>${l}</option>`).join('')}</select>
        <input id="fQ" type="search" placeholder="Search vendor, memo, id   ( / )" aria-label="Search" value="${esc(f.q)}">
        <span class="muted" id="rowCount" title="j and k move the selection"></span>
      </div>
      <div class="batchbar">
        <button class="btn" id="bcheck" data-act="bcheck">Check eligible</button>
        <span class="muted">Server gate on all pending items. Approves nothing.</span>
        <span class="grow"></span>
        <span class="dev"><span class="muted">Developer control</span>
          <button class="btn cheat" data-act="cheat">Try to cheat: force-approve ${CHEAT_ID}</button></span>
      </div>
      <div id="batchOut"></div>
      <div id="cheatOut"></div>
      <div class="tbl" id="tbl"></div>
    </div>
    <aside class="panel" id="panel" aria-label="Evidence"></aside>
  </div>`;
  drawBatch();
  get('/api/summary').then(s => { if (S.fin !== s.finalized) { S.fin = s.finalized; if ($('#panel') && cur() && decided(cur())) drawPanel(); } }).catch(() => {}); /* Reopen is hidden once the period is final */
  await loadTxns();
}

async function loadTxns() {
  const qs = new URLSearchParams({ limit: 500 });
  for (const k in S.filters) if (S.filters[k]) qs.set(k, S.filters[k]);
  const rf = S.filters.status === 'refund_requested'; if (rf) qs.set('status', 'approved');
  const r = await get('/api/txns?' + qs);
  S.txns = r.rows; S.total = r.total;
  /* ponytail: the API has no state filter, so refund requests are picked out of the first 500 approved rows; add a server filter if a period outgrows that */
  if (rf) { S.txns = r.rows.filter(t => t.state === 'refund_requested'); S.total = S.txns.length; }
  if (!S.txns.some(t => t.id === S.sel)) {
    const first = S.txns.find(t => t.risk_label === 'high' && t.status === 'pending') || S.txns[0];
    S.sel = first ? first.id : null;
  }
  drawTable(); drawPanel();
  const r2 = rowEl(S.sel); if (r2) r2.scrollIntoView({ block: 'nearest' });
}

const rowEl = id => $(`#tbl tr[data-id="${id}"]`);
const cur = () => S.txns.find(t => t.id === S.sel);
const riskChip = t => `<span class="chip r-${esc(t.risk_label)}">${esc(({ high: 'High', med: 'Med', low: 'Low' })[t.risk_label] || t.risk_label)} ${t.risk}</span>`;
const stCell = (t, fresh, long) => { const s = stOf(t);
  return t.status === 'approved'
  ? `<span class="st ok" title="${esc(STATE[s] || s)}"><svg class="tick${fresh ? ' fresh' : ''}"${fresh ? ` style="--d:${fresh.d}ms"` : ''} width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path pathLength="24" d="M3 8.5l3.5 3.5L13 4.5"/></svg>${s === 'refund_requested' ? (long ? STATE[s] : 'Refund asked') : 'Approved'}</span>`
  : s === 'cancelled' ? '<span class="st cx">Cancelled</span>' : s === 'routed' ? '<span class="st warn">Routed</span>' : '<span class="st">Pending</span>'; };
function rowHtml(t, fresh) {
  const n = t.flags.length, sel = t.id === S.sel;
  const cls = [sel && 'sel', t.status === 'cancelled' && 'cancelled'].filter(Boolean).join(' ');
  return `<tr data-act="sel" data-id="${esc(t.id)}"${cls ? ` class="${cls}"` : ''}${sel ? ' aria-selected="true"' : ''}>
    <td>${shortDate(t.date)}</td><td class="ell" title="${esc(t.vendor)}">${esc(t.vendor)}</td><td class="ell" title="${esc(t.category)}">${esc(t.category)}</td>
    <td class="r">${usd(t.amount_cents)}</td><td>${t.kind === 'activity' ? 'Activity' : 'Accounting'}</td><td>${riskChip(t)}</td>
    <td${n ? ` title="${esc(t.flags.map(f => f.code).join(', '))}"` : ' class="muted"'}>${n === 1 ? `<span class="mono">${esc(t.flags[0].code)}</span>` : n ? n + ' flags' : 'none'}</td><td>${stCell(t, fresh)}</td></tr>`;
}
function drawTable() {
  $('#rowCount').textContent = `${S.txns.length} of ${S.total} rows`;
  $('#tbl').innerHTML = S.txns.length ? `<table class="grid" aria-label="Transactions">
    <colgroup><col style="width:60px"><col><col style="width:140px"><col style="width:100px"><col style="width:92px"><col style="width:84px"><col style="width:108px"><col style="width:126px"></colgroup>
    <thead><tr><th>Date</th><th>Vendor</th><th>Category</th><th class="r">Amount</th><th>Kind</th><th>Risk</th><th>Flags</th><th>Status</th></tr></thead>
    <tbody>${S.txns.map(t => rowHtml(t)).join('')}</tbody></table>` : '<p class="empty">No transactions match these filters.</p>';
}
function select(id) {
  S.sel = id;
  $$('#tbl tr.sel').forEach(r => { r.classList.remove('sel'); r.removeAttribute('aria-selected'); });
  const r = rowEl(id); if (r) { r.classList.add('sel'); r.setAttribute('aria-selected', 'true'); r.scrollIntoView({ block: 'nearest' }); }
  drawPanel();
}
function move(d) {
  const i = S.txns.findIndex(t => t.id === S.sel), n = Math.min(Math.max(i + d, 0), S.txns.length - 1);
  if (S.txns[n]) select(S.txns[n].id);
}
async function gotoRow(id) {
  if (!S.txns.some(t => t.id === id)) { S.filters = { kind: '', status: '', risk: '', q: '' }; await review(); }
  select(id);
}
function patchApproved(ids, stagger) {
  ids.forEach((id, i) => {
    const t = S.txns.find(x => x.id === id); if (!t) return;
    t.status = t.state = 'approved'; t.approver = S.me.user.id;
    const r = rowEl(id); if (r) r.outerHTML = rowHtml(t, { d: stagger ? Math.min(i * 20, 300) : 0 });
  });
}

/* evidence panel */
function drawPanel() {
  const t = cur(), el = $('#panel');
  if (!t) { el.innerHTML = '<p class="empty">Select a row to see its evidence.</p>'; return; }
  const j = t.jev, low = j && j.confidence < 0.5, first = j && j.fraud >= 0.7 && !low;
  const needs = t.risk_label !== 'low';
  const canA = can() && t.preparer !== S.me.user.id;
  const why = !can() ? 'Only a controller or CFO can approve.' : t.preparer === S.me.user.id ? 'You prepared this item, so you cannot approve or cancel it (segregation of duties).' : '';
  const pend = t.status === 'pending' && can(), cash = cashExp(t), cx = t.status === 'cancelled', rw = resWords(t.resolution);
  S.prefill = '';
  el.innerHTML = `
  <div class="p-body">
    <header class="p-head">
      <div class="p-top"><span class="mono">${esc(t.id)}</span>${riskChip(t)}${stCell(t, null, true)}</div>
      <h2>${esc(t.vendor)}</h2>
      <div class="p-amt">${usd(t.amount_cents)}</div>
      <dl class="kv">
        <dt>Date</dt><dd>${esc(t.date)}, ${esc(t.category)}</dd>
        <dt>Source</dt><dd>${esc(t.kind)}, ${esc(t.source)}, <span class="mono">${esc(t.source_ref || 'no ref')}</span></dd>
        <dt>Prepared by</dt><dd>${esc(uname(t.preparer))}</dd>
        <dt>Memo</dt><dd class="ell1" title="${esc(t.memo)}">${esc(t.memo || 'none')}</dd>
      </dl>
    </header>
    <section class="p-sec"><h3>Rules (code)</h3>
      ${t.flags.length ? `<ul class="rules">${t.flags.map(f => `<li><code>${esc(f.code)}</code> ${esc(f.msg)}</li>`).join('')}</ul>` : '<p class="muted">No rules fired.</p>'}
    </section>
    <section class="p-sec"><h3>Screen (JEV, System 1)</h3>
      ${!j ? '<p class="muted">Not screened. The batch gate ignores JEV for this item.</p>' : `
      <div class="jev${low ? ' dim' : ''}">
        <div class="jev-top"><span class="jev-n">${Math.round(j.fraud * 100)}% anomalous</span>${first ? '<span class="chip bad">Review first</span>' : ''}${low ? '<span class="uncertain">uncertain, you decide</span>' : ''}</div>
        <div class="confbar" role="img" aria-label="confidence ${+j.confidence.toFixed(2)} of 1">${[1, 2, 3, 4, 5].map(i => `<i${i <= Math.round(j.confidence * 5) ? ' class="on"' : ''}></i>`).join('')}</div>
        <div class="jev-meta">confidence ${+j.confidence.toFixed(2)}, ${esc(String(j.risk_type).replace(/_/g, ' '))}, ${esc(j.origin)} ${j.latency_ms} ms</div>
      </div>`}
    </section>
    <section class="p-sec"><h3>Vendor history</h3><div id="p-vendor"></div></section>
    <section class="p-sec" id="p-act" hidden><h3>Routes and activity</h3><div id="p-actb"></div></section>
  </div>
  <footer class="p-foot">${decided(t)
    ? `<p class="approved-by${cx ? ' cx' : ''}"><strong>${cx ? 'Cancelled' : 'Approved'}</strong> by ${esc(uname(t.approver))}${t.approve_note ? ': ' + esc(t.approve_note) : '.'}</p>
       ${rw || t.effect_cents ? `<p class="dec small">${esc(rw)}${rw && t.effect_cents ? '. ' : ''}${t.effect_cents ? `${rw ? 'R' : cx ? 'Entry kept in the ledger, r' : 'R'}eview adjustment <strong>${usd(t.effect_cents)}</strong>` : ''}</p>` : ''}
       <div id="panelMsg" role="status"></div>
       ${can() && !S.fin ? '<div class="row"><button class="btn" data-act="reopen">Reopen</button><span class="muted small">Back to pending. Clears the decision.</span></div>' : ''}`
    : `${pend ? `<div class="reschips" role="group" aria-label="Approval reason">${Object.keys(RES).filter(k => k !== 'prepayment' || cash).map(k => `<button type="button" class="rchip" data-act="res" data-code="${k}" aria-pressed="false">${RES[k][0]}</button>`).join('')}
         <label class="mo" id="moWrap" hidden>months <input id="months" type="number" min="2" max="60" step="1" value="12"></label></div>` : ''}
       <label for="note">Note <span class="muted">${needs ? '(required, 5+ characters, for ' + (t.risk_label === 'high' ? 'high' : 'medium') + ' risk)' : pend ? '(optional to approve)' : '(optional)'}</span></label>
       <textarea id="note" rows="1">${esc(S.notes[t.id] || '')}</textarea>
       ${pend && cash ? `<div id="refundBox" hidden><label for="refundAmt">Amount to recover, USD</label><input id="refundAmt" type="number" min="0.01" step="0.01" value="${(t.amount_cents / 100).toFixed(2)}">
         <p class="muted small">Approves the payment as paid and opens an action item for the Payments desk. Uses the note above.</p></div>` : ''}
       <div id="panelMsg" role="status">${why ? `<span class="muted">${esc(why)}</span>` : ''}</div>
       <div class="row"><button class="btn primary" data-act="approve" ${canA ? '' : 'disabled'}>Approve <kbd>a</kbd></button>
       <button class="btn" data-act="route">Route <kbd>r</kbd></button>
       ${pend && cash ? `<button class="btn" data-act="refund" ${canA ? '' : 'disabled'}>Request refund</button>` : ''}
       ${pend && t.kind === 'accounting' ? `<button class="btn danger" data-act="cancelEntry" ${canA ? '' : 'disabled'}>Cancel entry</button>` : ''}</div>`}</footer>`;
  drawDetail(t.id);
  if (!S.detail[t.id]) loadDetail(t.id);
}
async function loadDetail(id) {
  try { S.detail[id] = await get('/api/txns/' + id); } catch (e) { S.detail[id] = { err: e.message }; }
  if (S.sel === id && $('#p-vendor')) drawDetail(id);
}
function drawDetail(id) {
  const d = S.detail[id], t = S.txns.find(x => x.id === id), v = $('#p-vendor');
  if (!v || !t) return;
  if (!d) { v.innerHTML = '<p class="muted">Loading vendor history...</p>'; return; }
  if (d.err) { v.innerHTML = `<p class="muted">${esc(d.err)}</p>`; return; }
  const vs = d.vendor, cs = d.category || {}, med = vs.median_cents || 0, amt = t.amount_cents, mx = Math.max(amt, med, 1);
  let hist = vs.history || [];
  if (hist.length > 24) { /* frequent vendor (hundreds of payments): one bar per period, its median payment, or the bars shrink to zero width */
    const by = {}; hist.forEach(p => (by[p.period] = by[p.period] || []).push(p.amount_cents));
    hist = Object.keys(by).sort().map(k => { const a = by[k].sort((x, y) => x - y); return { period: k, amount_cents: a[a.length >> 1] }; });
  }
  const hmax = Math.max(amt, med, ...hist.map(p => p.amount_cents), 1) * 1.25; /* headroom so the median line clears the bars */
  const off = t.risk_label === 'high'; /* styling only: the rules decide, the browser does not re-check */
  v.innerHTML = `<p class="vline"><span>First seen <strong>${esc(vs.first_seen)}</strong></span><span><strong>${num(vs.periods_seen)}</strong> period${vs.periods_seen === 1 ? '' : 's'} seen</span><span>Median <strong>${med ? usd(med) : 'none'}</strong></span></p>
    ${hist.length ? `<div class="spark" role="img" aria-label="Payments to this vendor in the last ${hist.length} periods, then this one; the dashed line is the vendor median">${hist.map(p => `<i style="height:${Math.max(3, p.amount_cents / hmax * 100)}%" title="${esc(p.period)}: ${usd(p.amount_cents)}"></i>`).join('')}<i class="cur${off ? ' off' : ''}" style="height:${Math.max(3, amt / hmax * 100)}%" title="This payment: ${usd(amt)}"></i>${med ? `<b class="medline" style="bottom:${med / hmax * 100}%" title="Vendor median ${usd(med)}"></b>` : ''}</div>
      <div class="spark-l small muted"><span>${esc(hist[0].period)}</span><span>dashed line: median</span><span>this payment</span></div>` : '<p class="muted">No earlier payments to this vendor.</p>'}
    ${cs.multiple_vs_median != null && cs.multiple_vs_median >= 1.5 ? `<p class="${t.flags.some(f => f.code === 'new_vendor' || f.code === 'outlier') ? 'warnline' : 'muted'}">This payment is ${+Number(cs.multiple_vs_median).toFixed(1)}x the ${esc(t.category)} median.</p>` : ''}`;
  const rs = d.routes || [], au = d.audit || [];
  $('#p-act').hidden = !(rs.length || au.length);
  $('#p-actb').innerHTML = rs.map(r => `<p class="route-line"><strong>${esc(REASONS[r.reason] || r.reason)}</strong> from ${esc(uname(r.from_user))} to ${esc(uname(r.to_user))}, ${esc(r.status)}. ${esc(r.note || '')}${r.reply ? ` <em>Reply: ${esc(r.reply)}</em>` : ''}</p>`).join('') +
    (au.length ? `<ul class="audl">${au.map(a => `<li><span class="mono">${when(a.ts)}</span> ${esc(uname(a.user_id))} ${esc(audName(a.action))}${a.detail ? ': ' + esc(a.detail) : ''}</li>`).join('')}</ul>` : '');
}
function panelMsg(cls, html) { const m = $('#panelMsg'); if (m) m.innerHTML = html ? `<div class="box ${cls}">${html}</div>` : ''; }

/* approve one */
async function approve() {
  const t = cur(); if (!t || decided(t) || !can() || t.preparer === S.me.user.id) return;
  const note = ($('#note') ? $('#note').value : '').trim();
  const chip = $('.rchip[aria-pressed=true]'), body = { ids: [t.id], note };
  if (chip) { body.resolution = { code: chip.dataset.code }; if (chip.dataset.code === 'prepayment') body.resolution.months = parseInt($('#months').value, 10); }
  if (t.risk_label !== 'low' && note.length < 5) {
    panelMsg('warn', `Add a note of 5 or more characters to approve a ${t.risk_label === 'high' ? 'high' : 'medium'} risk item.`);
    $('#note').focus(); return;
  }
  const btn = $('[data-act=approve]');
  await busy(btn, null, async () => {
    try {
      const r = await post('/api/approve', body);
      if (r.refused && r.refused.length) { panelMsg('bad', esc(r.refused.map(x => rsn(x.reasons)).join(' '))); return; }
      t.approve_note = note; delete S.notes[t.id];
      /* /api/approve answers ids only: read the row back so the resolution and its effect show */
      t.status = t.state = 'approved'; t.approver = S.me.user.id;
      try { const d = await get('/api/txns/' + t.id); Object.assign(t, d.txn); S.detail[t.id] = d; } catch (_) { delete S.detail[t.id]; }
      patchApproved(r.approved.filter(id => id !== t.id));
      const row = rowEl(t.id); if (row) row.outerHTML = rowHtml(t, { d: 0 });
      const i = S.txns.findIndex(x => x.id === t.id), order = S.txns.slice(i + 1).concat(S.txns.slice(0, i));
      const nx = order.find(x => x.status === 'pending');
      select(nx ? nx.id : t.id);
    } catch (e) { panelMsg('bad', esc(e.message) + ((e.data.refused || []).length ? ': ' + esc(e.data.refused.map(x => rsn(x.reasons)).join(' ')) : '')); }
  });
}

/* other decisions: cancel an accounting-only entry, ask Payments for a refund, reopen. The server returns the Txn. */
function applyTxn(x, msg) {
  const t = S.txns.find(y => y.id === x.id); if (!t) return;
  Object.assign(t, x); delete S.detail[t.id]; delete S.notes[t.id];
  const r = rowEl(t.id); if (r) r.outerHTML = rowHtml(t);
  if (S.sel === t.id) { drawPanel(); if (msg) panelMsg('ok', msg); }
}
const noteOr = what => { const n = ($('#note') ? $('#note').value : '').trim(); if (n.length >= 5) return n; panelMsg('warn', `Add a note of 5 or more characters to ${what}.`); if ($('#note')) $('#note').focus(); return null; };
function pickRes(el) {
  const on = el.getAttribute('aria-pressed') !== 'true';
  $$('.rchip').forEach(b => b.setAttribute('aria-pressed', b === el && on));
  const mw = $('#moWrap'); if (mw) mw.hidden = !(on && el.dataset.code === 'prepayment');
  if (on) prefill(el.dataset.code);
}
function prefill(code) { /* fills the note only when it is empty or still holds an earlier prefill */
  const n = $('#note'), text = RES[code][1]($('#months') ? $('#months').value : 12); if (!n) return;
  if (!n.value.trim() || n.value === S.prefill) { n.value = S.prefill = text; S.notes[S.sel] = text; }
}
async function cancelEntry(btn) {
  const t = cur(), note = t && noteOr('cancel an entry'); if (!note) return;
  await busy(btn, 'Cancelling', async () => {
    try { applyTxn((await post('/api/cancel', { id: t.id, note })).txn, 'Entry cancelled. It stays in the ledger and is taken out of the adjusted figures.'); }
    catch (e) { panelMsg('bad', esc(e.message)); }
  });
}
async function refund(btn) {
  const t = cur(), box = $('#refundBox'); if (!t || !box) return;
  if (box.hidden) { box.hidden = false; btn.textContent = 'Send refund request'; btn.classList.add('primary'); $('#refundAmt').select(); return; }
  const cents = Math.round(parseFloat($('#refundAmt').value) * 100);   // parses what the user typed; the server checks the range
  if (!(cents > 0)) { panelMsg('warn', 'Enter the amount to recover.'); $('#refundAmt').focus(); return; }
  const note = noteOr('request a refund'); if (!note) return;
  await busy(btn, 'Sending', async () => {
    try {
      const r = await post('/api/action', { id: t.id, type: 'refund_request', recover_cents: cents, note });
      applyTxn(r.txn, `Approved as paid. Refund request sent to ${esc((r.route && r.route.to_name) || 'the Payments desk')}.`);
    } catch (e) { panelMsg('bad', esc(e.message)); }
  });
}
async function reopen(btn) {
  const t = cur(); if (!t) return;
  await busy(btn, 'Reopening', async () => {
    try { applyTxn((await post('/api/reopen', { id: t.id })).txn, 'Reopened. The item is pending again.'); }
    catch (e) { panelMsg('bad', esc(e.message)); }
  });
}

/* route dialog */
function openRoute() {
  const t = cur(); if (!t || decided(t)) return;
  const dlg = $('#routeDlg');
  $('#rdId').textContent = t.id;
  $('#rdSub').textContent = `${t.vendor}, ${usd(t.amount_cents)}`;
  const others = S.users.filter(u => u.id !== S.me.user.id);
  $('#rdTo').innerHTML = others.map(u => `<option value="${esc(u.id)}">${esc(u.name)} (${esc(u.role)})</option>`).join('');
  const pref = others.find(u => u.role === 'cfo') || others[0]; if (pref) $('#rdTo').value = pref.id;
  $('#rdNote').value = ''; $('#rdMsg').hidden = true;
  dlg.showModal(); $('#rdNote').focus();
}
async function submitRoute() {
  const t = cur(), btn = $('#routeForm button[type=submit]');
  await busy(btn, 'Routing', async () => {
    try {
      await post('/api/route', { ids: [t.id], to_user: $('#rdTo').value, reason: $('#rdReason').value, note: $('#rdNote').value.trim() });
      $('#routeDlg').close();
      t.status = 'routed'; delete S.detail[t.id];
      const r = rowEl(t.id); if (r) r.outerHTML = rowHtml(t);
      drawPanel(); panelMsg('ok', `Routed to ${esc(uname($('#rdTo').value))}. It waits in their Inbox.`);
    } catch (e) { const m = $('#rdMsg'); m.hidden = false; m.textContent = e.message; }
  });
}

/* batch */
async function bcheck(btn) {
  await busy(btn, 'Checking', async () => {
    try { S.batch = await post('/api/batch-check', {}); S.batchDone = null; drawBatch(); }
    catch (e) { $('#batchOut').innerHTML = `<div class="box bad" role="alert">${esc(e.message)}</div>`; }
  });
}
function drawBatch() {
  const b = S.batch, out = $('#batchOut'); if (!out) return;
  if (!b) { out.innerHTML = ''; return; }
  const was = $('details', out); const open = was ? was.open : false;
  const label = id => { const t = S.txns.find(x => x.id === id); return t ? `${esc(t.vendor)}, ${usd(t.amount_cents)}` : ''; };
  const n = b.eligible.length, d = S.batchDone;
  out.innerHTML = `<div class="bres">
    <div class="bres-l">
      <details${open ? ' open' : ''}><summary><strong>${n} ready</strong></summary>
        <ul class="idlist">${b.eligible.map(id => `<li><button type="button" class="linkbtn mono" data-act="goto" data-id="${esc(id)}">${esc(id)}</button> ${label(id)}</li>`).join('')}</ul></details>
      ${d ? `<p class="okline"><strong>Approved ${d.approved.length}.</strong> Server re-checked all ${d.sent}.${d.refused.length ? ` It refused ${d.refused.length}: ${esc(d.refused.map(x => x.id).join(', '))}.` : ''}</p>`
        : `<button class="btn primary" data-act="bapprove" ${n ? '' : 'disabled'}>Approve ${n}</button>`}
    </div>
    <div class="bres-r"><strong>${b.held.length} held</strong>
      <ul class="held">${b.held.map(h => `<li><button type="button" class="linkbtn mono" data-act="goto" data-id="${esc(h.id)}">${esc(h.id)}</button> ${label(h.id)}<br><span class="reason">${esc(rsn(h.reasons))}</span></li>`).join('')}</ul>
    </div></div>`;
}
async function bapprove(btn) {
  const ids = S.batch.eligible.slice();
  await busy(btn, 'Approving', async () => {
    try {
      const r = await post('/api/batch-approve', { ids });
      patchApproved(r.approved, true);
      S.batchDone = { sent: ids.length, approved: r.approved, refused: r.refused || [] };
      drawBatch(); if (cur()) drawPanel();
    } catch (e) { $('#batchOut').insertAdjacentHTML('beforeend', `<div class="box bad" role="alert">${esc(e.message)}</div>`); }
  });
}
async function cheat(btn) {
  const out = $('#cheatOut');
  await busy(btn, 'Sending', async () => {
    try {
      const r = await post('/api/batch-approve', { ids: [CHEAT_ID] });
      patchApproved(r.approved || []);
      out.innerHTML = `<div class="box bad" role="alert"><strong>The server approved ${esc(CHEAT_ID)}.</strong> That should not happen. Tell the developer.</div>`;
    } catch (e) {
      const rs = (e.data.refused || []);
      out.innerHTML = `<div class="box bad" role="alert"><strong>Server refused (${e.status}).</strong> ${esc(e.data.error || e.message)}
        <ul>${rs.map(x => `<li><span class="mono">${esc(x.id)}</span>: ${esc(rsn(x.reasons))}</li>`).join('')}</ul>
        <span class="small">The browser cannot force it. The server re-runs the gate on every request.</span>
        <button class="linkbtn" data-act="cheatClose">Dismiss</button></div>`;
    }
  });
}

/* ---------- inbox ---------- */
async function inbox() {
  const r = await get('/api/inbox');
  S.inboxN = r.open.length; const nb = $('#inboxN'); if (nb) nb.textContent = S.inboxN || '';
  const txl = t => `<ul class="txl">${t.txns.map(x => `<li><a class="mono" href="#/review" data-act="open" data-id="${esc(x.id)}">${esc(x.id)}</a> ${esc(x.vendor)} <span class="r">${usd(x.amount_cents)}</span></li>`).join('')}</ul>`;
  const rfq = x => x.reason === 'refund_request';
  const recover = x => rfq(x) ? `<p class="recover"><strong>Recover ${x.recover_cents != null ? usd(x.recover_cents) : 'the overpayment'}</strong> <span class="muted">from the vendor. The payment is already approved as paid, so this does not block the close.</span></p>` : '';
  $('#main').innerHTML = `<h1>Inbox</h1>
  ${r.open.length ? r.open.map(x => `<article class="sheet route">
      <header><strong>${esc(x.from_name)}</strong> ${rfq(x) ? 'asks the Payments desk' : 'asks you'} <span class="chip ${rfq(x) ? 'warn' : 'neutral'}">${esc(REASONS[x.reason] || x.reason)}</span><span class="grow"></span><span class="muted">${when(x.ts)} UTC</span></header>
      ${txl(x)}${recover(x)}<p class="note">${esc(x.note)}</p>
      <label for="rp-${x.id}">${rfq(x) ? 'What happened with the vendor' : 'Your reply'}</label><textarea id="rp-${x.id}" rows="2"></textarea>
      <button class="btn primary" data-act="reply" data-id="${x.id}" data-rf="${rfq(x) ? 1 : ''}">${rfq(x) ? 'Reply and close the action item' : 'Reply and resolve'}</button>
      <div class="rmsg" id="rm-${x.id}" role="status"></div></article>`).join('')
    : '<p class="empty">Nothing routed. Press r on a row.</p>'}
  ${r.sent.length ? `<h2>Sent by you</h2>${r.sent.map(x => `<article class="sheet route sent">
      <header>To <strong>${esc(x.to_name)}</strong> <span class="chip neutral">${esc(REASONS[x.reason] || x.reason)}</span><span class="chip ${x.status === 'open' ? 'warn' : 'ok'}">${esc(x.status)}</span><span class="grow"></span><span class="muted">${when(x.ts)} UTC</span></header>
      ${txl(x)}${recover(x)}<p class="note">${esc(x.note)}</p>${x.reply ? `<p class="reply"><strong>Reply:</strong> ${esc(x.reply)}</p>` : ''}</article>`).join('')}` : ''}`;
}
async function reply(btn) {
  const id = btn.dataset.id, text = $('#rp-' + id).value.trim();
  if (!text) { $('#rm-' + id).innerHTML = '<span class="box warn">Write a reply first.</span>'; return; }
  await busy(btn, 'Sending', async () => {
    try { const r = await post(`/api/route/${id}/reply`, { text, resolve: true }); S.detail = {}; await inbox(); flash(btn.dataset.rf ? 'Reply sent. The refund action item is closed.' : `Reply sent. ${((r.route || {}).txns || []).map(x => esc(x.id)).join(', ') || 'The item'} is back with ${esc(uname((r.route || {}).from_user))} as pending.`); }
    catch (e) { $('#rm-' + id).innerHTML = `<span class="box bad">${esc(e.message)}</span>`; }
  });
}

/* ---------- insights ---------- */
function destroyCharts() { S.charts.forEach(c => c.destroy()); S.charts = []; }
/* Facts come from code as raw keys (cents, pct, driver names). Group and label them; the browser only formats. */
const pct = (v, sign) => (sign && v > 0 ? '+' : '') + Number(v).toFixed(1) + '%';
const usdS = c => (c > 0 ? '+' : '') + usd(c);
const DRIVER = { headcount: ['Headcount', 'head'], customers: ['Customers', 'customer'] };
function factGroups(f) {
  const dn = DRIVER[f.driver] || [String(f.driver || 'Driver'), 'unit'];
  const has = k => f[k] != null;
  const both = (c, p) => has(c) ? (has(p) ? `${usdS(f[c])} <span class="muted">(${pct(f[p], true)})</span>` : usdS(f[c])) : null;
  const G = [
    ['Spend', [['This month', has('current_cents') && usd(f.current_cents)], ['Prior month', has('prior_cents') && usd(f.prior_cents)], ['Budget', has('budget_cents') && usd(f.budget_cents)], ['3-month average', has('avg3_cents') && usd(f.avg3_cents)]]],
    ['Variance', [['vs prior month', both('variance_prior_cents', 'pct_vs_prior')], ['vs budget', both('variance_budget_cents', 'pct_vs_budget')], ['vs 3-month average', both('variance_avg3_cents', 'pct_vs_avg3')]]],
    [`Driver: ${dn[0]}`, [['This month', has('driver_current') && num(f.driver_current)], ['Prior month', has('driver_prior') && num(f.driver_prior)], ['Change', has('driver_pct') && pct(f.driver_pct, true)]]],
    ['What explains the change', [['Volume effect', has('volume_effect_cents') && usdS(f.volume_effect_cents)], ['Rate effect', has('rate_effect_cents') && usdS(f.rate_effect_cents)], ['Volume share of change', has('volume_share_pct') && pct(f.volume_share_pct)]]],
    [`Spend per ${dn[1]}`, [['This month', has('per_driver_current_cents') && usd(f.per_driver_current_cents)], ['Prior month', has('per_driver_prior_cents') && usd(f.per_driver_prior_cents)], ['Change', has('per_driver_pct') && pct(f.per_driver_pct, true)]]],
    ['Largest vendor', [['Vendor', has('top_vendor') && esc(f.top_vendor)], ['Change vs prior month', has('top_vendor_delta_cents') && usdS(f.top_vendor_delta_cents)], ['Share of the change', has('top_vendor_share_pct') && pct(f.top_vendor_share_pct)]]],
    ['Other drivers', [['Headcount', has('headcount_current') && `${num(f.headcount_current)} from ${num(f.headcount_prior)} <span class="muted">(${pct(f.headcount_pct, true)})</span>`], ['Occupancy', has('occupancy_pct_current') && `${pct(f.occupancy_pct_current)} from ${pct(f.occupancy_pct_prior)} <span class="muted">(${pct(f.occupancy_change_pct, true)})</span>`]]],
  ];
  return G.map(([t, rows]) => [t, rows.filter(r => r[1] !== false && r[1] != null && r[1] !== '')]).filter(g => g[1].length);
}
const factsHtml = f => factGroups(f).map(([t, rows]) => `<h4>${esc(t)}</h4><dl class="facts">${rows.map(([l, v]) => `<dt>${esc(l)}</dt><dd>${v}</dd>`).join('')}</dl>`).join('');
function safeMarked(html) { /* keep text and <mark class=unverified> only */
  const tpl = document.createElement('template'); tpl.innerHTML = html;
  return [...tpl.content.childNodes].map(n => n.nodeName === 'MARK' ? `<mark class="unverified">${esc(n.textContent)}</mark>` : esc(n.textContent)).join('');
}
const markFigures = (text, un) => un.reduce((h, u) => h.split(esc(u)).join(`<mark class="unverified">${esc(u)}</mark>`), esc(text));

async function insights() {
  const r = S.ins = await get('/api/insights');
  r.insights.forEach(i => {
    const c = i.commentary;
    if (S.cm[i.id]) S.cm[i.id].stale = !!c.stale;   // the server decides this on every load
    S.cm[i.id] = S.cm[i.id] || { stale: !!c.stale, enhanced: c.enhanced, marked: c.enhanced ? safeMarked(c.marked_html || '') : '', unverified: c.unverified || [], origin: '', use: c.enhanced && c.final === c.enhanced ? 'enhanced' : 'draft', pinned: c.pinned, draft: c.draft, err: '' };
  });
  $('#main').innerHTML = `<h1>Insights</h1>
    <p class="lede muted">Code computes every number. Write your commentary first; the AI only polishes your words and every figure it adds is checked.</p>
    <nav class="jump" aria-label="Insight cards">${r.insights.map(i => `<button type="button" class="btn" data-act="jump" data-id="${esc(i.id)}">${esc(i.category)}<span class="jp" id="jp-${esc(i.id)}"></span></button>`).join('')}<button type="button" class="btn" data-act="jump" data-id="kept">Kept out (${r.kept_out.length})</button></nav>
    ${r.insights.map(card).join('')}
    <section class="sheet kept" id="ins-kept"><h2>Kept out of the deck</h2>
      ${r.kept_out.length ? `<ul class="keptl">${r.kept_out.map(k => `<li><strong>${esc(k.title)}</strong> <span class="r">${usd(k.amount_cents)}</span><br><span class="muted">${esc(k.reason)}</span>${k.txn_id ? ` <button type="button" class="linkbtn small" data-act="top" data-id="${esc(k.txn_id)}">Open ${esc(k.txn_id)} in Review</button>` : ''}</li>`).join('')}</ul>` : '<p class="muted">Nothing was filtered out.</p>'}
    </section>`;
  r.insights.forEach(i => { drawChart(i); drawResult(i.id); });
  /* a stale AI summary is refreshed once per facts version when the tab opens; the button stays for a retry */
  if (canWrite() && (S.me.ai || {}).system2 !== 'off') r.insights.filter(i => i.narrative.stale && S.regen[i.id] !== i.facts_hash).forEach(i => { S.regen[i.id] = i.facts_hash; regen($(`[data-act=regen][data-id="${i.id}"]`)); });
}
function narHtml(i) {
  const n = i.narrative, off = S.me.ai && S.me.ai.system2 === 'off';
  return `<div class="nar-top">${n.origin === 'ai' ? '<span class="chip neutral" title="Wording by Claude (System 2). Every figure was checked against the computed facts.">AI</span>' : `<span class="chip warn" title="${off ? 'AI is offline.' : 'No checked AI text for these facts.'} This wording comes from a code template built only from the facts.">${off ? 'Template (AI offline)' : 'Template'}</span>`}
    ${n.stale ? `<span class="chip warn">Out of date: review decisions changed these facts</span>${canWrite() ? `<button class="btn sm" data-act="regen" data-id="${esc(i.id)}">Regenerate AI summary</button>` : ''}` : ''}</div>
    ${i.narErr ? `<div class="box bad" role="alert">${esc(i.narErr)}</div>` : ''}
    <p><strong>What.</strong> ${esc(n.what)}</p><p><strong>Context.</strong> ${esc(n.context)}</p><p><strong>Why.</strong> ${esc(n.why)}</p>`;
}
async function regen(btn) {
  if (!btn) return;
  const id = btn.dataset.id, i = S.ins.insights.find(x => x.id === id);
  await busy(btn, 'Updating AI summary', async () => {
    try { i.narrative = (await post(`/api/insights/${id}/regenerate`)).narrative; i.narErr = ''; } catch (e) { i.narErr = e.message; }
    const el = $('#nar-' + id); if (el) el.innerHTML = narHtml(i);   /* only the narrative block: the commentary box keeps what was typed */
  });
}
/* Booked, adjustments and adjusted all come from the facts. The browser adds nothing up. */
const adjStrip = f => f.adjustment_cents ? `<dl class="adj"><div><dt>Booked</dt><dd>${usd(f.current_cents)}</dd></div><div><dt>Review adjustments</dt><dd>${usdS(f.adjustment_cents)}</dd></div><div><dt>Adjusted</dt><dd>${f.adjusted_cents != null ? usd(f.adjusted_cents) : ''}</dd></div></dl>` : '';
function reviewHtml(f) {
  const it = f.review_items || [], n = f.review_open_count || 0;
  if (!it.length && !n) return '';
  return `<h4>Review decisions</h4>${it.length ? `<ul class="rvl">${it.map(x => { const code = x.resolution && (x.resolution.code || x.resolution), rw = resWords(code && { code, months: x.months, recover_cents: x.recover_cents });
    return `<li><button type="button" class="linkbtn mono" data-act="top" data-id="${esc(x.txn_id)}">${esc(x.txn_id)}</button> <span class="ell">${esc(x.vendor)}</span> <span class="r">${usd(x.amount_cents)}</span>
      <span class="rvd"><strong>${esc(STATE[x.state] || x.state)}</strong>${rw && x.state !== 'refund_requested' ? ', ' + esc(rw.toLowerCase()) : ''}${x.effect_cents ? `, effect ${usd(x.effect_cents)}` : ''}${x.by_name ? `, by ${esc(x.by_name)}` : ''}${x.note ? `: ${esc(x.note)}` : ''}</span></li>`; }).join('')}</ul>` : ''}
    ${n ? `<p class="warnline">${num(n)} item${n === 1 ? '' : 's'} still open in review${f.review_open_cents != null ? ', ' + usd(f.review_open_cents) : ''}.</p>` : ''}`;
}
const jump = id => { const el = $('#ins-' + id); if (el) el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' }); };
function card(i) {
  const c = S.cm[i.id], w = words(c.draft), ok = w >= 25;
  const tip = TIP;
  return `<article class="sheet ins" id="ins-${esc(i.id)}" data-id="${esc(i.id)}">
    <div class="ins-l">
      <header><div><h2>${esc(i.headline || i.title)}</h2>${i.headline ? `<p class="ins-sub muted">${esc(i.title)}</p>` : ''}</div><span class="grow"></span><a class="small" href="#/deck?slide=${esc(i.id)}">Open slide</a></header>
      <div class="nar" id="nar-${esc(i.id)}">${narHtml(i)}</div>
      ${adjStrip(i.facts)}
      <div class="chartbox"><canvas id="ch-${esc(i.id)}" role="img" aria-label="${esc(i.chart.title)}"></canvas></div>
    </div>
    <div class="ins-r"><h3>Facts, computed by code</h3>
      ${factsHtml(i.facts)}${reviewHtml(i.facts)}</div>
    <div class="cm">
      <label for="tx-${esc(i.id)}"><strong>Your commentary</strong> <span class="muted">Your words go into the deck. Enhance unlocks at 25 words.</span></label>
      <textarea id="tx-${esc(i.id)}" class="cm-tx" data-id="${esc(i.id)}" rows="4">${esc(c.draft)}</textarea>
      <div class="cm-bar"><span class="wc${ok ? ' ok' : ''}" id="wc-${esc(i.id)}">${w} / 25 words</span><span class="grow"></span>
        <button class="btn" id="en-${esc(i.id)}" data-act="enhance" data-mode="live" data-id="${esc(i.id)}" ${ok ? '' : `disabled title="${tip}"`}>Enhance with AI</button>
        <button class="btn" id="er-${esc(i.id)}" data-act="enhance" data-mode="recorded" data-id="${esc(i.id)}" ${ok ? '' : `disabled title="${tip}"`}>Recorded example, not live</button></div>
      <div class="cm-res" id="res-${esc(i.id)}"></div>
    </div></article>`;
}
function drawChart(i) {
  const cv = $('#ch-' + i.id); if (!cv) return;
  if (typeof Chart === 'undefined') { cv.parentNode.innerHTML = '<p class="muted">Chart library not loaded.</p>'; return; }
  const cs = getComputedStyle(document.documentElement), col = n => cs.getPropertyValue(n).trim();
  const pal = [col('--accent'), col('--ink-3'), col('--warn')], ch = i.chart, line = ch.type === 'line';
  Chart.defaults.font.family = cs.getPropertyValue('--font').trim() || 'sans-serif';
  S.charts.push(new Chart(cv, {
    type: ch.type,
    data: { labels: ch.labels, datasets: ch.series.map((s, k) => ({ label: s.name, data: s.values.map(v => v / 100), backgroundColor: pal[k % 3], borderColor: pal[k % 3], borderWidth: line ? 2 : 0, pointRadius: line ? 3 : 0, tension: 0 })) },
    options: {
      responsive: true, maintainAspectRatio: false, animation: reduced ? false : { duration: 400 },
      plugins: {
        legend: { display: ch.series.length > 1, labels: { color: col('--ink-2'), boxWidth: 10, font: { size: 12 } } },
        title: { display: true, text: ch.title, color: col('--ink-2'), font: { size: 12, weight: '600' } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${usd(c.parsed.y * 100)}` } },
      },
      scales: { x: { grid: { display: false }, ticks: { color: col('--ink-2'), font: { size: 12 } } }, y: { grid: { color: col('--rule') }, ticks: { color: col('--ink-2'), font: { size: 12 }, callback: v => '$' + (Math.abs(v) >= 1000 ? Math.round(v / 1000).toLocaleString('en-US') + 'k' : v) } } },
    },
  }));
}
function drawResult(id) {
  const c = S.cm[id], el = $('#res-' + id); if (!el) return;
  const nUn = c.unverified.length, hasDraft = c.draft.trim().length > 0;
  const jp = $('#jp-' + id); if (jp) jp.textContent = c.pinned ? ' (pinned)' : '';
  el.innerHTML = `
    ${c.err ? `<div class="box bad" role="alert">${esc(c.err)}</div>` : ''}
    ${c.stale && hasDraft ? '<p class="guard warn">Written before later review decisions. Re-read it, then pin again.</p>' : ''}
    ${c.enhanced ? `<div class="enh">
      <div class="enh-head"><strong>Enhanced version</strong>${c.origin ? `<span class="chip ${c.origin === 'recorded' ? 'warn' : 'neutral'}">${esc(c.origin === 'recorded' ? 'Recorded example, not live' : c.origin === 'live' ? 'Live' : 'Template')}</span>` : ''}</div>
      <p class="enh-text">${c.marked}</p>
      <p class="${nUn ? 'guard warn' : 'guard ok'}">${nUn ? `${nUn} figure${nUn === 1 ? '' : 's'} not verified. ${nUn === 1 ? 'It is' : 'They are'} not in the computed facts or in your text.` : 'Every figure is in the computed facts or in your text.'}</p>
      <div class="row"><button class="btn${c.use === 'enhanced' ? ' primary' : ''}" data-act="use" data-use="enhanced" data-id="${esc(id)}" aria-pressed="${c.use === 'enhanced'}">Accept enhanced</button>
        <button class="btn${c.use === 'draft' ? ' primary' : ''}" data-act="use" data-use="draft" data-id="${esc(id)}" aria-pressed="${c.use === 'draft'}">Keep mine</button></div>
    </div>` : ''}
    <div class="row pinrow"><button class="btn" data-act="pin" data-id="${esc(id)}" aria-pressed="${c.pinned}" ${hasDraft ? '' : 'disabled title="Write your commentary first."'}>${c.pinned ? 'Unpin' : 'Pin to deck'}</button>
      ${c.pinned ? `<span class="chip ok">Pinned, using ${c.use === 'enhanced' ? 'enhanced' : 'your words'}</span>` : '<span class="muted">Only pinned insights get your commentary on their slide.</span>'}</div>`;
}
function onDraft(ta) {
  const id = ta.dataset.id, c = S.cm[id], w = words(ta.value), ok = w >= 25;
  c.draft = ta.value;
  const wc = $('#wc-' + id); wc.textContent = `${w} / 25 words`; wc.classList.toggle('ok', ok);
  ['en-', 'er-'].forEach(p => { const b = $('#' + p + id); b.disabled = !ok; b.title = ok ? '' : TIP; });
  const pin = $(`[data-act=pin][data-id="${id}"]`); if (pin) pin.disabled = !ta.value.trim();
  clearTimeout(c.t); c.t = setTimeout(() => flush(id).catch(() => {}), 500);
}
const flush = id => { clearTimeout(S.cm[id].t); return post('/api/commentary', { insight_id: id, draft: S.cm[id].draft }).then(r => {
  const c = S.cm[id];   // the server drops the old enhancement (and the pin) when the words change: mirror that here
  if (r && r.changed) { Object.assign(c, { enhanced: '', marked: '', unverified: [], origin: '', use: 'draft' }); if (r.unpinned) { c.pinned = false; c.err = 'You edited after pinning. Pin again to put the new text in the deck.'; } else c.err = ''; drawResult(id); }
  return r;
}); };
async function enhance(btn) {
  const id = btn.dataset.id, c = S.cm[id];
  await busy(btn, 'Enhancing', async () => {
    try {
      await flush(id);
      const r = await post('/api/enhance', { insight_id: id, mode: btn.dataset.mode });
      Object.assign(c, { enhanced: r.enhanced, marked: safeMarked(r.marked_html), unverified: r.unverified || [], origin: r.origin, use: 'draft', err: '' });
      if (c.pinned) await post('/api/pin', { insight_id: id, pinned: true, use: 'draft' });
    } catch (e) { c.err = e.message; }
    drawResult(id);
  });
}
async function setUse(id, use) {
  const c = S.cm[id]; c.use = use; c.err = '';
  try { if (c.pinned) await post('/api/pin', { insight_id: id, pinned: true, use }); } catch (e) { c.err = e.message; }
  drawResult(id);
}
async function pin(btn) {
  const id = btn.dataset.id, c = S.cm[id];
  await busy(btn, null, async () => {
    try { await flush(id); await post('/api/pin', { insight_id: id, pinned: !c.pinned, use: c.use }); c.pinned = !c.pinned; c.err = ''; if (c.pinned) c.stale = false; }
    catch (e) { c.err = e.message; }
    drawResult(id);
  });
}

/* ---------- anchor ---------- */
const fp = (h, cls = 'ok') => `<span class="pill ${cls}" title="first 8 characters of the SHA-256">${esc(h || 'none')}</span>`;
const LINKS = { 'Begin transaction': 'First message on HashScan', 'Commit transaction': 'Last message on HashScan' };
const joinGlyph = '<svg class="join" width="44" height="18" viewBox="0 0 44 18" aria-hidden="true"><path d="M0 9h12M32 9h12"/><rect x="11" y="4" width="14" height="10" rx="5"/><rect x="19" y="4" width="14" height="10" rx="5"/></svg>';
async function anchor() {
  S.status = await get('/api/close/status');
  S.ready = null;
  if (!S.status.close) {
    const [sm, ins] = await Promise.all([get('/api/summary'), get('/api/insights').catch(() => null)]);
    S.ready = { open: sm.pending + sm.routed, pinned: ins ? ins.insights.filter(i => i.commentary.pinned).length : 0, own: ins ? ins.insights.some(i => i.commentary.pinned && i.commentary.words >= 25) : false };
  }
  drawAnchor();
}
const checks = () => { const r = S.ready; if (!r) return '';
  const row = (ok, text, sub, href, cta) => `<li class="${ok ? 'ok' : ''}"><span class="ck" aria-hidden="true">${ok ? '&#10003;' : ''}</span><span><strong>${text}</strong> <span class="muted">${sub}</span></span>${ok ? '' : `<a href="${href}">${cta}</a>`}</li>`;
  return `<ul class="checks" aria-label="What finalizing needs">
    ${row(r.open === 0, 'Every transaction approved or cancelled', r.open === 0 ? 'none open' : `${num(r.open)} still open`, '#/review', 'Open Review')}
    ${row(r.own, 'One pinned insight in your own words', r.own ? 'ready' : `${num(r.pinned)} pinned, needs 25 or more of your own words`, '#/insights', 'Write commentary')}
  </ul>`; };
function drawAnchor() {
  const st = S.status, c = st.close, hist = (st.history || []).filter(h => !c || h.close_id !== c.close_id);
  const prev = c && (hist.find(h => h.hash8 === c.prev_hash8) || {});
  const histHtml = hist.length ? `<section class="sheet"><h2>Earlier closes</h2><table class="grid small"><thead><tr><th>Period</th><th>Fingerprint</th><th>Topic</th><th>Closed by</th><th>Time (UTC)</th></tr></thead><tbody>
    ${hist.map(h => `<tr><td>${esc(h.period)}</td><td>${fp(h.hash8)}</td><td class="mono">${esc(h.topic_id)}</td><td>${esc(uname(h.closed_by))}</td><td>${when(h.ts)}</td></tr>`).join('')}</tbody></table></section>` : '';
  $('#main').innerHTML = `<h1>Anchor</h1>
    <p class="lede muted">Finalizing hashes the approved close and writes it to a public Hedera testnet topic. The anchor proves what was signed and when, not that it was right.</p>
    ${!c ? `<section class="sheet"><h2>Not finalized</h2>
        <p>Finalizing needs every item approved or cancelled and at least one pinned insight in your own words. Open refund requests do not block it.</p>
        ${checks()}
        <div class="row"><button class="btn primary" data-act="finalize">Finalize close</button><span class="muted" id="finStatus" role="status"></span></div>
        <div id="blockers">${blockersHtml()}</div></section>`
      : `<article class="receipt" aria-label="Close receipt">
        <header><h2>Close ${esc(c.period)}</h2><span class="chip ${c.tier === 'full' ? 'ok' : 'warn'}">${c.tier === 'full' ? 'Full payload on chain' : 'Hash only on chain'}</span></header>
        <div class="rc-l"><div class="fps"><div class="fpc">${fp(c.hash8)}<span class="muted">this close</span></div>${joinGlyph}
          <div class="fpc">${fp(c.prev_hash8)}<span class="muted">${prev && prev.period ? 'close ' + esc(prev.period) : 'previous close'}</span></div></div>
        <p class="hash mono" aria-label="Full SHA-256"><span class="h1">${esc(c.hash.slice(0, 16))}</span><span class="h2">${esc(c.hash.slice(16))}</span></p>
        <dl class="rc"><dt>Topic id</dt><dd class="mono">${esc(c.topic_id)}</dd><dt>Messages</dt><dd>${num(c.messages)}</dd><dt>Tier</dt><dd>${esc(c.tier)}</dd>
          <dt>Size on chain</dt><dd>${num(c.bytes)} bytes</dd><dt>Closed by</dt><dd>${esc(uname(c.closed_by))}</dd><dt>Time (UTC)</dt><dd>${when(c.ts)}</dd></dl>
        <p class="links"><a href="https://hashscan.io/testnet/topic/${esc(c.topic_id)}" target="_blank" rel="noopener">Topic on HashScan</a>${(c.links || []).map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(LINKS[l.label] || l.label)}</a>`).join('')}</p></div>
        <div class="rc-r"><div class="row acts">
          <button class="btn primary" data-act="verify">Verify against chain</button>
          <button class="btn" data-act="tamper">Simulate QuickBooks edit</button>
          <button class="btn" data-act="restore">Restore from chain</button>
          <span class="muted" id="anchorStatus" role="status">${esc(S.busy)}</span></div>
        <div id="tamperOut">${tamperHtml()}</div><div id="verifyOut">${verifyHtml()}</div><div id="restoreOut">${restoreHtml()}</div></div>
      </article>`}
    ${histHtml}`;
}
const blockersHtml = () => S.blockers ? `<div class="box bad" role="alert"><strong>${esc(S.blockers.error)}</strong>${S.blockers.list.length ? `<ul>${S.blockers.list.map(b => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}</div>` : '';
const FIELD = { amount_cents: 'amount', approve_note: 'approval note', approve_ts: 'approval time', source_ref: 'source reference', __row__: 'row' };
const fieldName = f => FIELD[f] || String(f).replace(/_/g, ' ');
const money = (f, v) => /cents$/.test(f) ? usd(v) : v == null ? 'missing' : esc(v);
const tamperHtml = () => S.tamper ? `<div class="box warn">Edited ${esc(S.tamper.id)} directly in the database: ${esc(fieldName(S.tamper.field))} ${money(S.tamper.field, S.tamper.was)} became ${money(S.tamper.field, S.tamper.now)}. Now verify.</div>` : '';
function verifyHtml() {
  const v = S.verify; if (!v) return '';
  return v.ok ? `<div class="box ok"><strong class="verdict">Matches chain</strong> ${fp(v.chain_hash8)} <span class="muted">checked in ${v.ms} ms via ${esc(v.via)}</span></div>`
    : `<div class="box bad" role="alert"><div class="vrow"><strong class="verdict">Mismatch</strong><span class="vp">on chain ${fp(v.chain_hash8, 'ok')}</span><span class="vp">live data ${fp(v.live_hash8, 'bad')}</span></div>
      <ul class="changed">${v.changed.length ? v.changed.map(x => x.field === '__section__' ? `<li>section <span class="mono">${esc(x.id)}</span> changed after the close</li>` : `<li>txn <span class="mono">${esc(x.id)}</span>: ${esc(fieldName(x.field))} ${money(x.field, x.was)} became ${money(x.field, x.now)}</li>`).join('') : `<li>${v.record_ok === false ? 'The local close record does not match the chain.' : 'Data differs from the anchored package.'}</li>`}</ul>
      <span class="small">Checked in ${v.ms} ms via ${esc(v.via)}.</span></div>`;
}
const restoreHtml = () => { const r = S.restore; return !r ? '' : `<div class="box ${r.ok && r.hash_ok ? 'ok' : 'bad'}"><strong>${r.ok ? 'Restored' : 'Restore failed'}</strong> ${num(r.txns)} transactions, ${num(r.bytes)} bytes, hash ${r.hash_ok ? 'verified against the chain' : 'did not match'}.
  <span class="links"><a href="${esc(r.files.json)}" download>Download JSON</a><a href="${esc(r.files.csv)}" download>Download CSV</a></span></div>`; };
async function chainCall(btn, msg, fn) {
  await busy(btn, null, async () => {
    S.busy = msg; $('#anchorStatus').textContent = msg;
    try { await fn(); } catch (e) { $('#verifyOut').innerHTML = `<div class="box bad" role="alert">${esc(e.message)} <button class="linkbtn" data-act="retry-chain">Dismiss</button></div>`; }
    S.busy = ''; const s = $('#anchorStatus'); if (s) s.textContent = '';
  });
}

/* ---------- audit ---------- */
async function audit() {
  const rows = await get('/api/audit?limit=100');
  const tgt = t => { try { const j = JSON.parse(t); if (Array.isArray(j)) return j.join(', '); } catch (_) { /* plain text */ } return t; };
  const flag = a => a.action === 'external_edit' || a.action === 'demo_shortcut' ? ' class="warnl"' : '';   // cancel, refund request and reopen read as plain labels via audName
  $('#main').innerHTML = `<h1>Audit</h1>${rows.length ? `<div class="sheet auditw"><table class="grid small"><colgroup><col style="width:150px"><col style="width:150px"><col style="width:150px"><col style="width:210px"><col></colgroup><thead><tr><th>Time (UTC)</th><th>User</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>
    ${rows.map(a => `<tr${flag(a)}><td class="mono">${when(a.ts)}</td><td>${esc(uname(a.user_id))}</td><td>${esc(audName(a.action))}</td><td class="mono ell" title="${esc(tgt(a.target))}">${esc(tgt(a.target))}</td><td class="ell" title="${esc(a.detail)}">${esc(a.detail)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">No activity recorded yet.</p>'}`;
}

/* ---------- demo menu: reset + shortcut (two-click confirm) ---------- */
const armed = (btn, armLabel) => {
  if (btn.dataset.armed) return true;
  const old = btn.textContent;
  btn.dataset.armed = '1'; btn.textContent = armLabel; btn.classList.add('armed');
  setTimeout(() => { if (btn.isConnected && btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = old; btn.classList.remove('armed'); } }, 4000);
  return false;
};
const closeMenu = () => { const d = $('#demoMenu'); if (d) d.open = false; };
async function doReset(btn) {
  if (!armed(btn, 'Click again to confirm')) return;
  await busy(btn, 'Resetting', async () => {
    try {
      await post('/api/reset');
      Object.assign(S, { txns: [], sel: null, detail: {}, notes: {}, batch: null, batchDone: null, ins: null, cm: {}, regen: {}, fin: false, status: null, verify: null, restore: null, tamper: null, blockers: null, filters: { kind: '', status: '', risk: '', q: '' } });
      closeMenu();
      history.replaceState(null, '', '#/cockpit'); await route();
      flash('Demo data reset to the seeded September close.');
    } catch (e) { closeMenu(); fail(e, route); }
  });
}
async function shortcut(btn) {
  if (!armed(btn, 'Click again to approve all open')) return;
  await busy(btn, 'Approving', async () => {
    try {
      const r = await post('/api/demo/complete-review');
      Object.assign(S, { txns: [], sel: null, detail: {}, batch: null, batchDone: null });
      closeMenu();
      await route();
      flash(`<strong>Demo shortcut used.</strong> ${num(r.approved)} approved, ${num(r.resolved_routes)} route${r.resolved_routes === 1 ? '' : 's'} resolved. An audit entry was written. Next: finalize on the Anchor tab. It needs one insight pinned in your own words.`, 'warn');
    } catch (e) { closeMenu(); flash(esc(e.message), 'bad'); }
  });
}

/* ---------- events ---------- */
const acts = {
  retry: () => (S.retry || route)(),
  'retry-chain': () => { $('#verifyOut').innerHTML = verifyHtml(); },
  logout: async () => { try { sessionStorage.setItem('cc_out', '1'); } catch (_) { /* storage blocked */ } try { await post('/api/logout'); } catch (_) { /* already out */ } S.me = null; S.loginUser = null; route(); },
  reset: el => doReset(el), shortcut: el => shortcut(el),
  persona: el => { S.loginUser = el.dataset.id; login(); setTimeout(() => $('#lpw') && $('#lpw').focus(), 0); },
  kind: el => { S.filters.kind = el.dataset.v; $$('.seg button').forEach(b => b.setAttribute('aria-pressed', b === el)); loadTxns().catch(e => fail(e, review)); },
  rvmode: el => { S.rvMode = el.dataset.v; route(); },
  jump: el => jump(el.dataset.id),
  sel: el => select(el.dataset.id),
  top: el => { S.sel = el.dataset.id; S.filters = { kind: '', status: '', risk: '', q: '' }; location.hash = '#/review'; },
  goto: el => gotoRow(el.dataset.id),
  open: el => { S.sel = el.dataset.id; S.filters = { kind: '', status: '', risk: '', q: '' }; },
  approve, route: openRoute, routeCancel: () => $('#routeDlg').close(),
  res: el => pickRes(el), cancelEntry, refund, reopen,
  bcheck, bapprove, cheat, cheatClose: () => { $('#cheatOut').innerHTML = ''; },
  reply,
  enhance, use: el => setUse(el.dataset.id, el.dataset.use), pin, regen,
  finalize: el => busy(el, 'Finalizing', async () => {
    const fs = $('#finStatus'); if (fs) fs.textContent = 'Writing to the Hedera testnet, about 5 seconds...';
    try { await post('/api/close/finalize'); S.blockers = null; S.verify = S.restore = S.tamper = null; S.status = await get('/api/close/status'); S.fin = true; drawAnchor(); }
    catch (e) { S.blockers = { error: e.message, list: (e.data && e.data.blockers) || [] }; $('#blockers').innerHTML = blockersHtml(); const f2 = $('#finStatus'); if (f2) f2.textContent = ''; }
  }),
  verify: el => chainCall(el, 'Checking Mirror Node...', async () => { S.verify = await post('/api/close/verify'); $('#verifyOut').innerHTML = verifyHtml(); }),
  tamper: el => chainCall(el, 'Editing the database...', async () => { S.tamper = await post('/api/demo/tamper'); S.verify = null; S.detail = {}; $('#tamperOut').innerHTML = tamperHtml(); $('#verifyOut').innerHTML = ''; }),
  restore: el => chainCall(el, 'Fetching from Mirror Node...', async () => { S.restore = await post('/api/close/restore'); $('#restoreOut').innerHTML = restoreHtml(); }),
};
document.addEventListener('click', e => {
  const dm = $('#demoMenu'); if (dm && dm.open && !dm.contains(e.target)) dm.open = false;
  const el = e.target.closest('[data-act]'); if (!el || !acts[el.dataset.act]) return;
  if (el.tagName === 'A' && el.dataset.act !== 'open') e.preventDefault();
  acts[el.dataset.act](el, e);
});
document.addEventListener('input', e => {
  const t = e.target;
  if (t.id === 'fQ') { clearTimeout(S.qt); S.qt = setTimeout(() => { S.filters.q = t.value.trim(); loadTxns().catch(er => fail(er, review)); }, 200); }
  else if (t.id === 'note' && S.sel) S.notes[S.sel] = t.value;
  else if (t.id === 'months') prefill('prepayment');
  else if (t.classList.contains('cm-tx')) onDraft(t);
});
document.addEventListener('change', e => {
  const t = e.target;
  if (t.id === 'fStatus' || t.id === 'fRisk') { S.filters[t.id === 'fStatus' ? 'status' : 'risk'] = t.value; loadTxns().catch(er => fail(er, review)); }
});
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
  if (e.key === 'Escape' && $('#demoMenu') && $('#demoMenu').open) { $('#demoMenu').open = false; return; }
  if (e.key === 'Escape') { if (typing) e.target.blur(); else if (S.tab === 'review') $('#cheatOut') && ($('#cheatOut').innerHTML = ''); return; }
  if (typing || S.tab !== 'review' || S.rvMode === 'cards' || $('#routeDlg').open || !S.txns.length) return;
  const k = e.key;
  if (k === 'j' || k === 'ArrowDown') { e.preventDefault(); move(1); }
  else if (k === 'k' || k === 'ArrowUp') { e.preventDefault(); move(-1); }
  else if (k === 'a') { e.preventDefault(); approve(); }  /* preventDefault: approve() may focus the note box, the keystroke must not land in it */
  else if (k === 'r') { e.preventDefault(); openRoute(); }
  else if (k === '/') { e.preventDefault(); $('#fQ').focus(); }
});

boot();
