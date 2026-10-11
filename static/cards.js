/* Close Commenter: card review. The second way to review held transactions, one at a time.
   Loaded before app.js: everything lives in this closure and app.js globals are touched at call time only.
   The server decides every outcome; this file shows what it answers. */
(() => {
'use strict';

const APPROVE = [['supported', 'Supported'], ['prepayment', 'Prepayment'], ['one_time', 'One-time'], ['timing', 'Timing']];
const REFUND = ['Double payment', 'Overpayment', 'Wrong vendor', 'Other'];
const CANCEL = ['Posted in error', 'Duplicate accrual', 'Already reversed', 'Wrong period', 'Other'];
const ROUTE = [['second_opinion', 'Second opinion'], ['investigation', 'Investigation'], ['clarification', 'Clarification']];
const KEYS = { ArrowRight: 'approve', a: 'approve', ArrowLeft: 'reject', x: 'reject', ArrowUp: 'route', r: 'route', ArrowDown: 'skip', s: 'skip', u: 'undo', z: 'undo' };
let C = null; /* state of the mounted view, null when unmounted */

/* ponytail: the browser cannot read engine.CATEGORIES, so "cash expense" is guessed from the category name to choose
   which chips to offer. The server enforces the real rule and its refusal is shown on the card. */
const cashExp = t => t.kind === 'activity' && !/revenue/i.test(t.category);
const rejKind = t => t.kind === 'accounting' ? 'cancel' : cashExp(t) ? 'refund' : null;
const cur = () => C.q[0];
const el = id => C.root.querySelector('#' + id);
const say = s => { const p = el('cdStatus'); if (p) p.textContent = s; };
const reasonsOf = t => t.held_reasons || [];
/* dollars typed by a person to integer cents, by string handling only */
const toCents = s => { const m = /^\$?\s*(\d[\d,]*)(?:\.(\d{1,2}))?$/.exec(String(s).trim()); return m ? parseInt(m[1].replace(/,/g, '') + (m[2] || '').padEnd(2, '0'), 10) : NaN; };
const SOURCE = { ap: 'AP', card: 'Card', bank: 'Bank', payroll: 'Payroll' };
const routeTo = () => S.users.find(u => u.role === (S.me.user.role === 'cfo' ? 'controller' : 'cfo')) || {};

/* why a decision is not available to this user on this card; '' when it is */
function gate(t, m) {
  if (m === 'route') return '';
  if (!can()) return 'Only a controller or CFO can approve.';
  if (t.preparer === S.me.user.id) return 'You prepared this item, so you cannot approve it (segregation of duties).';
  if (!C.det[t.id]) return 'Evidence is still loading.';
  if (m === 'reject' && !rejKind(t)) return 'A cash receipt cannot be refunded or cancelled here. Route it instead.';
  return '';
}
function chipsFor(t, m) {
  if (m === 'approve') return APPROVE.filter(c => c[0] !== 'prepayment' || cashExp(t)).map(([code, label]) => ({ code, label }));
  if (m === 'route') return ROUTE.map(([code, label]) => ({ code, label }));
  return (rejKind(t) === 'cancel' ? CANCEL : REFUND).map(label => ({ code: label === 'Other' ? 'other' : '', label }));
}
const stampFor = (t, m) => m === 'approve' ? 'Approve' : m === 'route' ? 'Route' : m === 'skip' ? 'Skip' : rejKind(t) === 'cancel' ? 'Cancel' : 'Action item';

/* ---------- queue ---------- */
async function loadQueue() {
  let rows = (await get('/api/txns?queue=held&limit=500')).rows || [];
  if (rows.some(t => t.held_reasons === undefined)) { /* queue=held not live on this server: ask the gate directly */
    const [b, all] = await Promise.all([post('/api/batch-check', {}), get('/api/txns?status=pending&limit=500')]);
    const why = {}; (b.held || []).forEach(h => { why[h.id] = h.reasons; });
    rows = (all.rows || []).filter(t => why[t.id]).map(t => ({ ...t, held_reasons: why[t.id] }))
      .sort((a, z) => z.risk - a.risk || Math.abs(z.amount_cents) - Math.abs(a.amount_cents));
  }
  return rows.filter(t => t.status === 'pending');
}
function loadDet(id) {
  const c = C; if (id in c.det) return;
  c.det[id] = null;
  get('/api/txns/' + id).then(d => d, e => ({ err: e.message })).then(d => {
    if (C !== c) return;
    c.det[id] = d;
    if (cur() && cur().id === id && !c.recap) { drawCard(); if (!c.mode) drawBar(); drawEv(); }
  });
}

/* ---------- mount ---------- */
async function cardsMount(root) {
  cardsUnmount();
  const c = C = { root, q: [], total: 0, det: {}, mode: null, pick: -1, done: [], undo: null, dollars: 0, rTotal: 0, rCleared: 0, locked: false, busy: false, msg: '', recap: false, shown: null };
  root.classList.add('cd-root');
  root.innerHTML = '<p class="empty">Loading the held items...</p>';
  root.addEventListener('click', onClick);
  root.addEventListener('pointerdown', onDown);
  document.addEventListener('keydown', onKey);
  try {
    const [rows, sum] = await Promise.all([loadQueue(), get('/api/summary').catch(() => ({}))]);
    if (C !== c) return;
    c.q = rows; c.total = rows.length; c.locked = !!sum.finalized;
    c.rTotal = rows.reduce((n, t) => n + reasonsOf(t).length, 0);
    root.innerHTML = `<div class="cd-strip" id="cdStrip"></div>
      <div class="cd-main">
        <div class="cd-col"><div id="cdCard" class="cd-slot"></div><div id="cdBar" class="cd-barslot"></div><p id="cdStatus" class="cd-status small" role="status" aria-live="polite"></p></div>
        <aside class="cd-ev" id="cdEv" aria-label="Evidence"></aside>
      </div>`;
    draw();
  } catch (e) {
    if (C === c) root.innerHTML = `<div class="box bad cd-fail" role="alert">${esc(e.message || 'Could not load the held items.')}</div>`;
  }
}
function cardsUnmount() {
  if (!C) return;
  document.removeEventListener('keydown', onKey);
  C.root.removeEventListener('click', onClick);
  C.root.removeEventListener('pointerdown', onDown);
  C = null;
}

/* ---------- render ---------- */
function draw() {
  drawStrip(); drawCard(); drawBar(); drawEv();
  C.q.slice(0, 3).forEach(t => loadDet(t.id));
}
function drawStrip() {
  const n = C.done.length, u = C.undo;
  el('cdStrip').innerHTML = `
    <div class="cd-prog"><span><strong>${num(n)} of ${num(C.total)}</strong> decided</span>
      <span class="cd-meter" role="progressbar" aria-label="Held items decided" aria-valuemin="0" aria-valuemax="${C.total}" aria-valuenow="${n}"><i style="width:${C.total ? n / C.total * 100 : 0}%"></i></span></div>
    <span class="cd-stat">Dollars reviewed this session <strong>${usd(C.dollars)}</strong></span>
    <span class="cd-stat">Held reasons cleared <strong>${num(C.rCleared)} of ${num(C.rTotal)}</strong></span>
    <span class="grow"></span>
    ${u ? `<button type="button" class="btn" data-cd="undo">Undo: ${esc(u.t.id)} ${esc(u.short)} <kbd>u</kbd></button>` : ''}
    ${C.total ? `<button type="button" class="btn" data-cd="recap" aria-pressed="${C.recap}">Recap</button>` : ''}`;
}
function ratioLine(t) {
  const d = C.det[t.id];
  if (!d) return 'Loading vendor history...';
  if (d.err) return esc(d.err);
  const med = d.vendor && d.vendor.median_cents, cs = d.category || {};
  /* display only: a rounded ratio so the reviewer sees the scale; the gate's own sentence is in the reasons below */
  if (med) return `about <strong>${+(Math.abs(t.amount_cents) / med).toFixed(1)}x</strong> this vendor's usual ${usd(med)}`;
  if (cs.multiple_vs_median != null) return `New vendor. About <strong>${+Number(cs.multiple_vs_median).toFixed(1)}x</strong> the ${esc(t.category)} median`;
  return 'New vendor, no history';
}
function drawCard() {
  const slot = el('cdCard'), t = cur();
  if (C.recap || !t) { slot.innerHTML = C.total ? recapHtml() : emptyHtml(); C.shown = null; return; }
  const rs = reasonsOf(t), j = t.jev, low = j && j.confidence < 0.5, pos = `${Math.min(C.done.length + 1, C.total)} of ${C.total}`;
  const mine = t.preparer === S.me.user.id, m = C.mode;
  const fresh = C.shown !== t.id; C.shown = t.id;
  slot.innerHTML = `
  <article class="cd-card${fresh ? ' cd-in' : ''}${m ? ' cd-park cd-park-' + m : ''}" tabindex="0" role="group" aria-label="${esc(`${pos}: ${t.vendor}, ${usd(t.amount_cents)}, ${t.risk_label} risk, ${rs.length} reason${rs.length === 1 ? '' : 's'} held`)}">
    <span class="cd-stamp cd-st-${m || 'none'}" aria-hidden="true">${m ? esc(stampFor(t, m)) : ''}</span>
    <header class="cd-top"><span class="mono">${esc(t.id)}</span><span class="chip neutral">${t.kind === 'activity' ? 'Cash: ' + esc(SOURCE[t.source] || t.source) : 'Accounting only'}</span>${riskChip(t)}<span class="grow"></span><span class="muted small">${pos}</span></header>
    <div class="cd-amtrow"><span class="cd-amt">${usd(t.amount_cents)}</span><span class="cd-ratio">${ratioLine(t)}</span></div>
    ${C.msg ? `<div class="box bad cd-refusal" role="alert"><strong>Not recorded.</strong> ${esc(C.msg)}</div>` : ''}
    <h2 class="cd-vendor">${esc(t.vendor)}</h2>
    <p class="cd-meta" title="${esc(t.memo || '')}">${shortDate(t.date)}, ${esc(t.category)}${t.memo ? '. ' + esc(t.memo) : ''}</p>
    <h3>Why it is held</h3>
    <ul class="cd-why">${rs.slice(0, 3).map(r => `<li>${esc(r)}</li>`).join('') || '<li class="muted">The gate gave no reason.</li>'}</ul>
    ${rs.length > 3 ? `<p class="small muted cd-more">and ${rs.length - 3} more, listed under Evidence</p>` : ''}
    <p class="cd-jev${low ? ' cd-dim' : ''}"><span class="muted">Screen (JEV):</span> ${j ? `${Math.round(j.fraud * 100)}% anomalous, confidence ${+Number(j.confidence).toFixed(2)}, ${esc(String(j.risk_type || '').replace(/_/g, ' '))}${low ? ' <em>uncertain, you decide</em>' : ''}` : 'Not screened'}</p>
    <footer class="cd-foot">Prepared by ${esc(uname(t.preparer))}.${mine ? ' <span class="cd-sod">You prepared this item, so you cannot approve it (segregation of duties).</span>' : ''}</footer>
  </article>`;
}
function drawBar() {
  const slot = el('cdBar'), t = cur(), m = C.mode;
  slot.inert = false;
  if (C.recap || !t) { slot.innerHTML = ''; return; }
  if (C.locked) { slot.innerHTML = '<div class="box warn cd-lock" role="alert">Period is finalized and locked. No decision can be recorded.</div>'; return; }
  if (!m) {
    const wa = gate(t, 'approve'), wr = gate(t, 'reject'), rk = rejKind(t);
    const why = [...new Set([wa, wr])].filter(w => w && w !== 'Evidence is still loading.').join(' ');
    slot.innerHTML = `<div class="cd-bar" role="group" aria-label="Decide this item">
      <button type="button" class="btn cd-b-rej" data-cd="mode" data-m="reject" ${wr ? 'disabled' : ''}>${rk === 'cancel' ? 'Cancel entry' : 'Refund request'} <kbd>x</kbd></button>
      <button type="button" class="btn" data-cd="mode" data-m="route">Route <kbd>r</kbd></button>
      <button type="button" class="btn" data-cd="skip">Skip <kbd>s</kbd></button>
      <button type="button" class="btn primary" data-cd="mode" data-m="approve" ${wa ? 'disabled' : ''}>Approve <kbd>a</kbd></button>
    </div><p class="cd-hint small muted">${esc(why) || 'Arrow keys work too. Every decision asks for a reason before it is sent.'}</p>`;
    return;
  }
  const chips = chipsFor(t, m), rk = rejKind(t), to = routeTo();
  const title = m === 'approve' ? 'Approve as' : m === 'route' ? `Route to ${esc(to.name || 'the CFO')} for` : rk === 'cancel' ? 'Cancel because' : 'Ask payments to recover';
  slot.innerHTML = `<div class="cd-pick" role="group" tabindex="-1" aria-label="${m === 'approve' ? 'Approve: pick a resolution' : m === 'route' ? 'Route: pick a reason' : 'Pick a reason'}. Number keys pick, Enter confirms, Escape goes back.">
    <strong class="cd-pick-t">${title}</strong>
    ${m === 'reject' && rk === 'refund' ? `<label class="cd-lab">$<input id="cdRecover" class="cd-input cd-money" inputmode="decimal" autocomplete="off" aria-label="Amount to recover in dollars" value="${esc(usd(Math.abs(t.amount_cents)).replace('$', ''))}"></label><span class="muted small">because</span>` : ''}
    ${chips.map((c, i) => `<button type="button" class="btn cd-chip" data-cd="chip" data-i="${i}" aria-pressed="false"><kbd>${i + 1}</kbd> ${esc(c.label)}</button>${c.code === 'prepayment' ? '<label class="cd-lab"><input id="cdMonths" class="cd-input cd-num" type="number" min="2" max="60" step="1" value="12" aria-label="Months prepaid"> months</label>' : ''}`).join('')}
    ${m === 'route' ? '<input id="cdNote" class="cd-input cd-wide" maxlength="200" autocomplete="off" aria-label="Short note for the reviewer" placeholder="Short note (optional)">' : ''}
    ${chips.some(c => c.code === 'other') ? '<input id="cdOther" class="cd-input cd-wide" maxlength="200" autocomplete="off" aria-label="Other reason" placeholder="Reason, 5 or more characters" hidden>' : ''}
    <span class="cd-pick-f"><button type="button" class="btn primary" data-cd="commit" disabled>Confirm <kbd>Enter</kbd></button><button type="button" class="btn" data-cd="back">Back <kbd>Esc</kbd></button></span>
    <span id="cdWarn" class="cd-warn small" role="alert"></span>
  </div>`;
}
function sparkHtml(t, d) {
  const vs = d.vendor || {}, cs = d.category || {}, med = vs.median_cents || 0, amt = Math.abs(t.amount_cents);
  let hist = (vs.history || []).map(p => ({ period: p.period, amount_cents: Math.abs(p.amount_cents) }));
  if (hist.length > 24) { /* frequent vendor: one bar per period (its median payment), so the bars stay visible */
    const by = {}; hist.forEach(p => (by[p.period] = by[p.period] || []).push(p.amount_cents));
    hist = Object.keys(by).sort().map(k => { const a = by[k].sort((x, y) => x - y); return { period: k, amount_cents: a[a.length >> 1] }; });
  }
  const hmax = Math.max(amt, med, ...hist.map(p => p.amount_cents), 1) * 1.25;
  return `<p class="vline"><span>First seen <strong>${esc(vs.first_seen || 'now')}</strong></span><span><strong>${num(vs.periods_seen || 0)}</strong> period${vs.periods_seen === 1 ? '' : 's'} seen</span><span>Median <strong>${med ? usd(med) : 'none'}</strong></span></p>
    ${hist.length ? `<div class="spark cd-spark" role="img" aria-label="Payments to this vendor in ${hist.length} earlier periods, then this one. The dashed line is the vendor median.">${hist.map(p => `<i style="height:${Math.max(3, p.amount_cents / hmax * 100)}%" title="${esc(p.period)}: ${usd(p.amount_cents)}"></i>`).join('')}<i class="cur${t.risk_label === 'high' ? ' off' : ''}" style="height:${Math.max(3, amt / hmax * 100)}%" title="This item: ${usd(amt)}"></i>${med ? `<b class="medline" style="bottom:${med / hmax * 100}%"></b>` : ''}</div>
      <div class="spark-l small muted"><span>${esc(hist[0].period)}</span><span>dashed line: median</span><span>this item</span></div>` : '<p class="muted">No earlier payments to this vendor.</p>'}
    ${cs.multiple_vs_median != null && cs.multiple_vs_median >= 1.5 ? `<p class="small muted">${+Number(cs.multiple_vs_median).toFixed(1)}x the ${esc(t.category)} median payment.</p>` : ''}`;
}
function drawEv() {
  const ev = el('cdEv'), t = cur();
  if (C.recap || !t) { ev.innerHTML = '<h2>Evidence</h2><p class="muted">No card is open.</p>'; return; }
  const d = C.det[t.id], rs = reasonsOf(t), routes = (d && d.routes) || [], au = (d && d.audit) || [];
  ev.innerHTML = `<h2>Evidence</h2>
    <p class="cd-ev-id"><span class="mono">${esc(t.id)}</span> ${esc(t.vendor)}</p>
    <section><h3>Vendor history</h3>${!d ? '<p class="muted">Loading vendor history...</p>' : d.err ? `<p class="muted">${esc(d.err)}</p>` : sparkHtml(t, d)}</section>
    <section><h3>Held by the gate (${rs.length})</h3><ul class="cd-list">${rs.map(r => `<li>${esc(r)}</li>`).join('')}</ul></section>
    <section><h3>Source</h3><dl class="kv"><dt>Entry</dt><dd>${esc(t.kind)}, ${esc(t.source)}, <span class="mono">${esc(t.source_ref || 'no ref')}</span></dd><dt>Date</dt><dd>${esc(t.date)}</dd><dt>Memo</dt><dd>${esc(t.memo || 'none')}</dd></dl></section>
    <section><h3>Routes</h3>${routes.length ? routes.map(r => `<p class="route-line"><strong>${esc(REASONS[r.reason] || String(r.reason).replace(/_/g, ' '))}</strong> from ${esc(r.from_name || uname(r.from_user))} to ${esc(r.to_name || uname(r.to_user))}, ${esc(r.status)}. ${esc(r.note || '')}${r.reply ? ` <em>Reply: ${esc(r.reply)}</em>` : ''}</p>`).join('') : '<p class="muted small">Not routed yet.</p>'}</section>
    <section><h3>Audit trail</h3>${au.length ? `<ul class="audl">${au.map(a => `<li><span class="mono">${when(a.ts)}</span> ${esc(uname(a.user_id))} ${esc(a.action)}${a.detail ? ': ' + esc(a.detail) : ''}</li>`).join('')}</ul>` : '<p class="muted small">No activity recorded yet.</p>'}</section>`;
}
function recapHtml() {
  const left = C.q.length, n = C.done.length;
  return `<section class="sheet cd-recap"><h2>${left ? 'Session recap' : 'Held queue decided'}</h2>
    <p class="muted">${num(n)} of ${num(C.total)} decided in this session. ${left ? `${num(left)} still held and pending.` : 'Nothing is held for you now.'} Each line is in the audit log.</p>
    ${n ? `<table class="cd-table"><thead><tr><th scope="col">Item</th><th scope="col">Decision</th><th scope="col">Note</th><th scope="col" class="r">Amount</th></tr></thead>
      <tbody>${C.done.map(x => `<tr><td><span class="mono">${esc(x.t.id)}</span><br><span class="small muted">${esc(x.t.vendor)}</span></td><td>${esc(x.action)}</td><td>${esc(x.note)}</td><td class="r">${usd(x.t.amount_cents)}</td></tr>`).join('')}</tbody></table>` : '<p>No decision recorded yet.</p>'}
    <div class="row cd-recap-f">${left ? '<button type="button" class="btn primary" data-cd="recap">Back to cards</button>' : ''}<a class="btn cd-link" href="#/audit">Open Audit</a><button type="button" class="btn" data-cd="table">Back to Table</button></div></section>`;
}
const emptyHtml = () => `<section class="sheet cd-recap"><h2>Nothing is held</h2><p>The gate holds no pending item for you. Eligible items wait under Table, Check eligible.</p><div class="row cd-recap-f"><button type="button" class="btn primary" data-cd="table">Back to Table</button></div></section>`;

/* ---------- interaction ---------- */
function focusCard() { const c = C.root.querySelector('.cd-card'); if (c) c.focus({ preventScroll: true }); }
function setMode(m) {
  const t = cur(); if (!t || C.locked || C.busy || C.recap) return;
  const why = gate(t, m);
  if (why) { say(why); return; }
  C.mode = m; C.pick = -1; C.msg = '';
  drawCard(); drawBar();
  const g = C.root.querySelector('.cd-pick'); if (g) { g.focus({ preventScroll: true }); g.scrollIntoView({ block: 'nearest' }); }
}
function back() {
  if (!C.mode || C.busy) return;
  C.mode = null; C.pick = -1;
  drawCard(); drawBar(); focusCard();
}
function pick(i, byClick) {
  const t = cur(); if (!t || !C.mode || C.busy) return;
  const chips = chipsFor(t, C.mode), c = chips[i]; if (!c) return;
  C.pick = i;
  C.root.querySelectorAll('.cd-chip').forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.i === i)));
  const other = el('cdOther'), go = C.root.querySelector('[data-cd=commit]');
  if (other) other.hidden = c.code !== 'other';
  if (go) { go.disabled = false; go.setAttribute('aria-label', 'Confirm: ' + c.label); }
  el('cdWarn').textContent = '';
  if (c.code === 'other') other.focus();
  else if (C.mode === 'route') { if (byClick) el('cdNote').focus(); }
  else if (byClick) commit(); /* a tapped chip is the reason: send it */
}
const warn = s => { const w = el('cdWarn'); if (w) w.textContent = s; };
async function commit() {
  const c = C, t = cur(), m = c.mode;
  if (!t || !m || c.busy || c.locked) return;
  const chip = chipsFor(t, m)[c.pick];
  if (!chip) return warn('Pick a reason first.');
  let note = chip.label, action, short = '', send, undoable = true;
  if (chip.code === 'other') {
    note = el('cdOther').value.trim();
    if (note.length < 5) { el('cdOther').focus(); return warn('Write a reason of 5 or more characters.'); }
  }
  if (m === 'approve') {
    const resolution = { code: chip.code };
    if (chip.code === 'prepayment') {
      const mo = Number(el('cdMonths').value);
      if (!Number.isInteger(mo) || mo < 2 || mo > 60) { el('cdMonths').focus(); return warn('Months must be a whole number from 2 to 60.'); }
      resolution.months = mo; note = `Prepayment, ${mo} months`;
    }
    action = 'Approved'; short = 'approved'; send = () => post('/api/approve', { ids: [t.id], note, resolution });
  } else if (m === 'route') {
    const to = routeTo();
    if (!to.id) return warn('No reviewer is available to route to.');
    note = el('cdNote').value.trim() || chip.label;
    action = `Routed to ${to.name}`; undoable = false;
    send = () => post('/api/route', { ids: [t.id], to_user: to.id, reason: chip.code, note });
  } else if (rejKind(t) === 'cancel') {
    action = 'Cancelled'; short = 'cancelled'; send = () => post('/api/cancel', { id: t.id, note });
  } else {
    const recover_cents = toCents(el('cdRecover').value);
    if (!(recover_cents > 0)) { el('cdRecover').focus(); return warn('Enter the amount to recover, for example 1,250.00.'); }
    if (recover_cents > Math.abs(t.amount_cents)) { el('cdRecover').focus(); return warn(`Recover at most the amount paid, ${usd(Math.abs(t.amount_cents))}.`); }
    action = `Refund of ${usd(recover_cents)} requested`; short = 'refund request';
    send = () => post('/api/action', { id: t.id, type: 'refund_request', recover_cents, note });
  }
  c.busy = true; el('cdBar').inert = true; warn('Sending...');
  let refusal = '';
  try {
    const r = await send();
    if (r && r.refused && r.refused.length) refusal = r.refused.map(x => rsn(x.reasons)).join(' ');
  } catch (e) {
    if (e.status === 409 && /final|lock/i.test(e.message || '')) c.locked = true;
    refusal = (e.message || 'Request failed') + (e.data && (e.data.refused || []).length ? ': ' + e.data.refused.map(x => rsn(x.reasons)).join(' ') : '');
  }
  if (C !== c) return;
  c.busy = false; c.mode = null; c.pick = -1;
  if (refusal) { /* nothing is counted; the card comes back with the server's reason */
    c.msg = refusal; say(''); /* the alert box on the card is announced by itself */
    drawCard(); drawBar(); focusCard(); return;
  }
  const entry = { t, action, short, note, undoable };
  c.q.shift(); c.done.push(entry); c.undo = undoable ? entry : null; c.msg = '';
  c.dollars += Math.abs(t.amount_cents); /* session tally of the amounts on decided cards, shown as "reviewed" only */
  c.rCleared += reasonsOf(t).length;
  const nx = cur();
  say(`${action}: ${t.id}, ${note}. ${nx ? `Next: ${nx.vendor}, ${usd(nx.amount_cents)}.` : 'The held queue is decided.'}`);
  draw(); focusCard();
}
function skip() {
  const t = cur(); if (!t || C.busy || C.recap) return;
  if (C.q.length < 2) { say('This is the only item left in the queue.'); return; }
  C.q.push(C.q.shift()); C.mode = null; C.pick = -1; C.msg = '';
  say(`Skipped ${t.id}. It stays pending, at the back of the queue.`);
  draw(); focusCard();
}
async function undo() {
  const c = C, u = c.undo; if (!u || c.busy) return;
  c.busy = true;
  try {
    const r = await post('/api/reopen', { id: u.t.id });
    if (C !== c) return;
    c.done.splice(c.done.indexOf(u), 1);
    c.q.unshift({ ...u.t, ...(r.txn || {}), held_reasons: u.t.held_reasons });
    c.dollars -= Math.abs(u.t.amount_cents); c.rCleared -= reasonsOf(u.t).length;
    c.undo = null; c.mode = null; c.recap = false; c.msg = ''; delete c.det[u.t.id];
    say(`Reopened ${u.t.id}. It is pending again.`);
    draw(); focusCard();
  } catch (e) {
    if (C !== c) return;
    say('Undo refused. ' + (e.message || '')); flash(esc('Undo refused. ' + (e.message || '')), 'bad');
  } finally { c.busy = false; }
}
function onClick(e) {
  const b = e.target.closest('[data-cd]'); if (!b || !C || b.disabled) return;
  const a = b.dataset.cd;
  if (a === 'mode') setMode(b.dataset.m);
  else if (a === 'skip') skip();
  else if (a === 'chip') pick(+b.dataset.i, true);
  else if (a === 'commit') commit();
  else if (a === 'back') back();
  else if (a === 'undo') undo();
  else if (a === 'recap') { if (C.busy) return; C.recap = !C.recap; C.mode = null; draw(); }
  else if (a === 'table') { const tb = document.querySelector('.seg [data-v="table"]'); if (tb) tb.click(); }
}
function onKey(e) {
  if (!C || !C.root.isConnected || e.metaKey || e.ctrlKey || e.altKey) return;
  const tg = e.target, typing = /^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName) || tg.isContentEditable;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (typing) { /* only Enter and Escape act while typing, and only in this view's own fields */
    if (!C.root.contains(tg)) return;
    if (k === 'Enter') { e.preventDefault(); commit(); } else if (k === 'Escape') back();
    return;
  }
  const dlg = document.querySelector('dialog[open]'); if (dlg) return;
  if (k === 'Enter') { if (/^(BUTTON|A|SUMMARY)$/.test(tg.tagName)) return; if (C.mode) { e.preventDefault(); commit(); } return; }
  if (k === 'Escape') { if (C.mode) back(); else if (C.recap && C.q.length) { C.recap = false; draw(); } return; }
  if (C.mode && /^[1-5]$/.test(k)) { e.preventDefault(); pick(+k - 1, false); return; }
  const act = KEYS[k]; if (!act) return;
  e.preventDefault();
  if (act === 'skip') skip(); else if (act === 'undo') undo(); else setMode(act);
}
/* drag: 1:1 with the pointer. Release never posts; it opens the same reason chips the buttons open. */
function onDown(e) {
  const card = e.target.closest('.cd-card');
  if (!card || !C || e.button || C.locked || C.busy || C.mode || e.target.closest('button, input, a')) return;
  const t = cur(), x0 = e.clientX, y0 = e.clientY, stamp = card.querySelector('.cd-stamp');
  let dir = null, blocked = '';
  const mv = ev => {
    let dx = ev.clientX - x0, dy = ev.clientY - y0;
    const horiz = Math.abs(dx) >= Math.abs(dy), m = horiz ? (dx > 0 ? 'approve' : 'reject') : (dy < 0 ? 'route' : 'skip');
    blocked = horiz ? gate(t, m) : '';
    if (blocked) { dx = Math.max(-40, Math.min(40, dx)); dy = 0; }
    const dist = horiz ? Math.abs(dx) : Math.abs(dy);
    dir = !blocked && dist >= (horiz ? 120 : 90) ? m : null;
    if (!reduced) card.style.transform = `translate(${dx}px, ${dy}px) rotate(${Math.max(-12, Math.min(12, dx / 20))}deg)`;
    stamp.textContent = blocked ? 'Not allowed' : stampFor(t, m);
    stamp.className = 'cd-stamp cd-st-' + (blocked ? 'skip' : m);
    stamp.style.opacity = dist >= 40 ? Math.min(1, dist / 120) : 0;
  };
  const up = () => {
    card.removeEventListener('pointermove', mv); card.removeEventListener('pointerup', up); card.removeEventListener('pointercancel', up);
    card.classList.remove('cd-drag'); card.style.transform = ''; stamp.style.opacity = ''; stamp.textContent = ''; stamp.className = 'cd-stamp cd-st-none';
    if (!C) return;
    if (dir === 'skip') skip(); else if (dir) setMode(dir); else if (blocked) say(blocked);
  };
  card.classList.add('cd-drag');
  try { card.setPointerCapture(e.pointerId); } catch (_) { /* synthetic pointer */ }
  card.addEventListener('pointermove', mv); card.addEventListener('pointerup', up); card.addEventListener('pointercancel', up);
}

window.cardsMount = cardsMount;
window.cardsUnmount = cardsUnmount;
})();
