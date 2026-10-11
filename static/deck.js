'use strict';
/* Deck tab: a 16:9 preview of the month-end close deck. One GET /api/deck; this file formats and lays out, it never sums money.
   Loaded before app.js: app.js globals ($, $$, esc, num, get, S) are used inside functions only. */

const DK = { ac: '#16201C', pm: '#8A938D', hist: '#B9BFBA', fav: '#1E6B45', unfav: '#B3261E', neutral: '#66706A', ink2: '#46504B' };
const DK_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const DK_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
let dkCharts = [], dkIds = [], dkI = 0, dkOn = false;

/* ---- formatting only: $ thousands on charts and tables, full dollars in transaction lists, negatives in parentheses ---- */
const dkAbs = (n, d = 0) => Math.abs(Number(n)).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const dkNo = v => v == null || v === '' || Number.isNaN(Number(v));
const dkK = c => dkNo(c) ? 'n/a' : c < 0 ? `($${dkAbs(c / 1e5)}k)` : `$${dkAbs(c / 1e5)}k`;
const dkKs = c => dkNo(c) ? 'n/a' : c < 0 ? `($${dkAbs(c / 1e5)}k)` : `+$${dkAbs(c / 1e5)}k`;
const dkT = c => dkNo(c) ? '' : c < 0 ? `(${dkAbs(c / 1e5)})` : dkAbs(c / 1e5);
const dkTs = c => dkNo(c) ? '' : c < 0 ? `(${dkAbs(c / 1e5)})` : `+${dkAbs(c / 1e5)}`;
const dkD = c => dkNo(c) ? 'n/a' : c < 0 ? `($${dkAbs(c / 100)})` : `$${dkAbs(c / 100)}`;
const dkP = v => dkNo(v) ? '' : v < 0 ? `(${dkAbs(v, 1)}%)` : `+${dkAbs(v, 1)}%`;
const dkPp = v => dkNo(v) ? 'n/a' : v < 0 ? `(${dkAbs(v, 1)}%)` : `${dkAbs(v, 1)}%`;
const dkMon = p => /^\d{4}-\d{2}/.test(p || '') ? `${DK_MON[+p.slice(5, 7) - 1]} ${p.slice(2, 4)}` : String(p || '');
const dkCls = f => f === true ? 'dk-fav' : f === false ? 'dk-unfav' : 'dk-neu';
const dkTag = f => f === true ? '<span class="dk-tag dk-fav">fav</span>' : f === false ? '<span class="dk-tag dk-unfav">unfav</span>' : '';
const dkDir = v => v < 0 ? 'down' : 'up';
const dkWrap = s => { /* axis labels: break long names into lines of about 12 characters */
  const out = ['']; String(s || '').split(' ').forEach(w => { const l = out[out.length - 1]; if (l && (l + ' ' + w).length > 12) out.push(w); else out[out.length - 1] = l ? l + ' ' + w : w; });
  return out.length > 1 ? out : out[0];
};

/* ---- the one chart plugin: values written at the bar ends, no legend, no tooltip ---- */
const dkLabelPlugin = {
  id: 'dkLabels',
  afterDatasetsDraw(chart) {
    const ctx = chart.ctx, horiz = chart.options.indexAxis === 'y';
    ctx.save();
    chart.data.datasets.forEach((ds, di) => {
      if (!ds.dkLabels) return;
      const meta = chart.getDatasetMeta(di), line = meta.type === 'line';
      ctx.font = `600 ${ds.dkSize || 14}px ${DK_FONT}`;
      ctx.fillStyle = ds.dkInk || DK.ac;
      meta.data.forEach((el, i) => {
        const t = ds.dkLabels[i]; if (t == null) return;
        if (line) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(t, el.x + 26, el.y); }
        else if (horiz) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(t, Math.max(el.x, el.base) + 6, el.y); }   /* always right of the bar, clear of the category names */
        else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(t, el.x, Math.min(el.y, el.base) - 6); }
      });
    });
    ctx.restore();
  },
};
const dkOpts = extra => Object.assign({
  responsive: false, animation: false, devicePixelRatio: 2, events: [],
  plugins: { legend: { display: false }, tooltip: { enabled: false }, title: { display: false } },
}, extra);
const dkX = size => ({ grid: { display: false }, border: { color: DK.ac, width: 1 }, ticks: { color: DK.ink2, font: { size, family: DK_FONT }, maxRotation: 0, autoSkip: false } });
const dkY = () => ({ display: false, beginAtZero: true });
function dkChart(id, cfg) {
  const cv = document.getElementById(id); if (!cv) return;
  if (typeof Chart === 'undefined') { cv.insertAdjacentHTML('afterend', '<p class="dk-note">Chart library not loaded.</p>'); return; }
  try { cfg.plugins = [dkLabelPlugin]; dkCharts.push(new Chart(cv, cfg)); }
  catch (e) { cv.insertAdjacentHTML('afterend', '<p class="dk-note">This chart could not be drawn.</p>'); }
}

/* A. prior month, budget, actual */
function dkChartTriplet(id, f, pmL, acL) {
  dkChart(id, { type: 'bar',
    data: { labels: [`${pmL} PM`, `${acL} BU`, `${acL} AC`], datasets: [{
      data: [f.prior_cents, f.budget_cents, f.current_cents], backgroundColor: [DK.pm, '#FFFFFF', DK.ac], borderColor: [DK.pm, DK.ac, DK.ac],
      borderWidth: [0, 1.5, 0], borderSkipped: false, barPercentage: 0.5, categoryPercentage: 0.9,
      dkLabels: [dkK(f.prior_cents), dkK(f.budget_cents), dkK(f.current_cents)] }] },
    options: dkOpts({ layout: { padding: { top: 30, left: 40, right: 40 } }, scales: { x: dkX(14), y: dkY() } }) });
}
/* B. 13 months, close month in ink, budget dashed */
function dkChartTrend(id, s) {
  const n = s.labels.length, last = (a, pre) => a.map((v, i) => i === n - 1 && v != null ? pre + dkK(v) : null);
  dkChart(id, { type: 'bar',
    data: { labels: s.labels.map(dkMon), datasets: [
      { data: s.actual_cents, backgroundColor: s.labels.map((_, i) => i === n - 1 ? DK.ac : DK.hist), barPercentage: 0.8, categoryPercentage: 0.9, order: 2, dkLabels: last(s.actual_cents, '') },
      { type: 'line', data: s.budget_cents || [], borderColor: DK.neutral, borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0, tension: 0, order: 1, dkInk: DK.neutral, dkSize: 13, dkLabels: last(s.budget_cents || [], 'BU ') },
    ] },
    options: dkOpts({ layout: { padding: { top: 30, right: 96 } }, scales: { x: dkX(12), y: dkY() } }) });
}
/* C. waterfall. Bar positions are layout; every label is a figure the server sent. */
function dkChartBridge(id, b) {
  const L = [], D = [], col = [], lab = []; let pos = 0;
  const tot = t => { L.push(dkWrap(t.label)); D.push([0, t.value_cents]); col.push(DK.ac); lab.push(dkK(t.value_cents)); pos = t.value_cents; };
  const step = s => { L.push(dkWrap(s.label)); D.push([pos, pos + s.delta_cents]); col.push(s.favorable === true ? DK.fav : s.favorable === false ? DK.unfav : DK.neutral); lab.push(dkKs(s.delta_cents)); pos += s.delta_cents; };
  tot(b.start); (b.steps || []).forEach(step); tot(b.booked);
  if ((b.adjustments || []).length) { b.adjustments.forEach(step); tot(b.end); }
  dkChart(id, { type: 'bar',
    data: { labels: L, datasets: [{ data: D, backgroundColor: col, borderSkipped: false, minBarLength: 2, barPercentage: 0.72, categoryPercentage: 0.9, dkLabels: lab }] },
    options: dkOpts({ layout: { padding: { top: 30 } }, scales: { x: dkX(L.length > 10 ? 12 : 13), y: dkY() } }) });
}
/* D. variance to budget by category, coloured by impact */
function dkChartVariance(id, rows) {
  dkChart(id, { type: 'bar',
    data: { labels: rows.map(r => r.label), datasets: [{
      data: rows.map(r => r.var_budget_cents), backgroundColor: rows.map(r => r.fav_budget === true ? DK.fav : r.fav_budget === false ? DK.unfav : DK.neutral),
      barPercentage: 0.7, categoryPercentage: 0.9, dkSize: 12, dkLabels: rows.map(r => dkKs(r.var_budget_cents)) }] },
    options: dkOpts({ indexAxis: 'y', layout: { padding: { right: 4 } }, scales: {
      x: { beginAtZero: true, grace: '45%', ticks: { display: false }, border: { display: false }, grid: { drawTicks: false, lineWidth: 1, color: c => c.tick && c.tick.value === 0 ? DK.ac : 'transparent' } },
      y: { grid: { display: false }, border: { display: false }, ticks: { color: DK.ac, font: { size: 12, family: DK_FONT }, autoSkip: false } } } }) });
}

/* ---- slides ---- */
const DK_STATE = { pending: ['Pending', ''], routed: ['Routed', 'dk-warn'], approved: ['Approved', 'dk-ok'], cancelled: ['Cancelled', 'dk-bad'], refund_requested: ['Refund requested', 'dk-warn'], open: ['Open', 'dk-warn'], replied: ['Replied', 'dk-ok'], withdrawn: ['Withdrawn', ''] };
const DK_ADJ = { cancelled: 'Cancelled entry', refund_request: 'Refund request', prepayment: 'Prepayment spread', route: 'Routed' };
const dkState = (st, it) => {
  const code = it && (typeof it.resolution === 'string' ? it.resolution : (it.resolution || {}).code);
  if (st === 'approved' && code === 'prepayment') return `<span class="dk-st dk-ok">Prepaid${it.months ? ' ' + esc(it.months) + ' mo' : ''}</span>`;
  const m = DK_STATE[st] || [String(st || '').replace(/_/g, ' '), ''];
  return `<span class="dk-st ${m[1]}">${esc(m[0])}</span>`;
};
const dkRow = (l, v, cls = '') => `<div class="dk-row ${cls}"><b>${esc(l)}</b><span>${v}</span></div>`;

function dkSlides(d) {
  const isFinal = d.status === 'final', all = d.insights || [];
  const ins = isFinal ? all.filter(i => i.commentary && i.commentary.pinned) : all;
  const K = Object.fromEntries((d.kpis || []).map(k => [k.key, k]));
  const rows = (d.pnl && d.pnl.rows) || [], tot = (d.pnl && d.pnl.totals) || {};
  const rowBy = Object.fromEntries(rows.map(r => [r.label, r]));
  const slideNo = Object.fromEntries(ins.map((i, k) => [i.category, 5 + k]));   /* cover, summary, P&L, bridge, then insights */
  const tl = (d.trend && d.trend.labels) || [], acL = dkMon(d.period), pmL = tl.length > 1 ? dkMon(tl[tl.length - 2]) : 'Prior';
  const c = d.controls || {}, open = d.open_items || [], adj = c.adjustments || [];
  const out = [];

  /* 1. cover and sign-off */
  const cl = d.close;
  out.push({ id: 'cover', title: 'Cover and sign-off', cls: 'dk-cover', head: '', body: `
    <div>
      <span class="dk-status ${isFinal ? 'dk-final' : ''}">${isFinal ? 'Final' : 'Draft, not signed'}</span>
      <p class="dk-cover-co">${esc(d.company)}</p>
      <h2 class="dk-cover-t">${esc(d.period_label)}<br>month-end close</h2>
      <p class="dk-cover-s">${isFinal
        ? `Signed close. This deck carries the ${num(ins.length)} insight${ins.length === 1 ? '' : 's'} with pinned commentary.`
        : `Working draft. Every material insight is shown (${num(ins.length)}). The fingerprint changes until the close is finalized, and the final deck carries pinned commentary only.`}
        ${d.synthetic ? ' All figures are synthetic demo data.' : ''}</p>
    </div>
    <dl class="dk-sign">
      <div><dt>Prepared by</dt><span>${esc(d.prepared_by || 'n/a')}</span></div>
      <div><dt>Signed by</dt><span>${cl ? esc(cl.closed_by_name) + ', ' + esc(String(cl.ts || '').slice(0, 10)) : 'Not signed'}</span></div>
      <div><dt>Fingerprint</dt><span class="dk-mono">${cl ? 'sha256 ' + esc(cl.hash8) : 'no fingerprint yet'}</span></div>
      <div><dt>Units</dt><span>${esc(d.units || 'USD')}, $ thousands unless stated</span></div>
    </dl>` });

  /* 2. executive summary */
  const r = K.result, o = K.opex, rv = K.revenue, hc = K.headcount, mg = K.margin, cu = K.customers;
  const tile = k => !k ? '' : `<div class="dk-tile"><div class="dk-lbl">${esc(k.label)}</div><div class="dk-val">${dkK(k.value_cents)}</div>
    <div class="dk-dl"><b>PM</b><span>${dkK(k.prior_cents)}</span><i></i><b>&Delta;PM</b><span class="${dkCls(k.fav_prior)}">${dkKs(k.var_prior_cents)} &nbsp;${dkP(k.var_prior_pct)}</span><i>${dkTag(k.fav_prior)}</i>
    <b>BU</b><span>${dkK(k.budget_cents)}</span><i></i><b>&Delta;BU</b><span class="${dkCls(k.fav_budget)}">${dkKs(k.var_budget_cents)} &nbsp;${dkP(k.var_budget_pct)}</span><i>${dkTag(k.fav_budget)}</i></div>
    <div class="dk-adj">${k.adjusted_cents != null && k.adjusted_cents !== k.value_cents ? `Adjusted ${dkK(k.adjusted_cents)} after review decisions` : ''}</div></div>`;
  const minor = (k, val, line) => !k ? '' : `<div class="dk-tile dk-minor"><div class="dk-lbl">${esc(k.label)}</div><div class="dk-val">${val}</div><div class="dk-note">${line}</div></div>`;
  out.push({ id: 'summary',
    title: r ? `Operating ${r.value_cents < 0 ? 'loss' : 'profit'} of ${dkK(Math.abs(r.value_cents))}, ${r.fav_prior ? 'better' : 'worse'} than ${d.prior_label} by ${dkK(Math.abs(r.var_prior_cents))}` : 'Executive summary',
    rail: 'Executive summary',
    sub: [o && `Operating expenses ${dkDir(o.var_prior_pct)} ${dkAbs(o.var_prior_pct, 1)}%`, rv && `revenue ${dkDir(rv.var_prior_pct)} ${dkAbs(rv.var_prior_pct, 1)}%`, hc && `headcount ${num(hc.prior)} to ${num(hc.value)}`].filter(Boolean).join(', ') + ` vs ${d.prior_label}`,
    body: `<div class="dk-sum"><div class="dk-tiles">${tile(rv)}${tile(o)}${tile(r)}
      ${minor(mg, mg && dkPp(mg.value_pct), mg && `Prior ${dkPp(mg.prior_pct)}, budget ${dkPp(mg.budget_pct)}. Change ${dkNo(mg.var_prior_pts) ? 'n/a' : (mg.var_prior_pts < 0 ? '(' + dkAbs(mg.var_prior_pts, 1) + ')' : '+' + dkAbs(mg.var_prior_pts, 1))} pts ${dkTag(mg.fav_prior)}`)}
      ${minor(hc, hc && num(hc.value), hc && `Prior ${num(hc.prior)}, ${dkP(hc.var_prior_pct)}`)}
      ${minor(cu, cu && num(cu.value), cu && `Prior ${num(cu.prior)}, ${dkP(cu.var_prior_pct)}`)}
      ${c.total != null ? `<div class="dk-stat"><div class="dk-lbl">Review status</div><p>${num(c.approved)} of ${num(c.total)} transactions approved, ${num(c.cancelled || 0)} cancelled, ${num(c.pending)} pending. ${num(c.flagged_open || 0)} flagged still open. ${num(open.length)} action item${open.length === 1 ? '' : 's'} open. Net effect of review decisions: ${dkD(c.adjustment_total_cents || 0)}.</p></div>` : ''}</div>
      <div class="dk-heads"><div class="dk-lbl">Material variances</div>
      ${ins.slice(0, 7).map((i, k) => `<button type="button" class="dk-hl" data-go="${4 + k}"><span>${5 + k}</span><span>${esc(i.headline || i.title)}</span></button>`).join('') || '<p class="dk-none-yet">No material variances in this deck.</p>'}</div></div>` });

  /* 3. P&L with variance bars */
  const cells = x => `<td>${dkT(x.actual_cents)}</td><td>${dkT(x.prior_cents)}</td><td class="${dkCls(x.fav_prior)}">${dkTs(x.var_prior_cents)}${dkTag(x.fav_prior)}</td><td class="${dkCls(x.fav_prior)}">${dkP(x.var_prior_pct)}</td>
    <td>${dkT(x.budget_cents)}</td><td class="${dkCls(x.fav_budget)}">${dkTs(x.var_budget_cents)}${dkTag(x.fav_budget)}</td><td class="${dkCls(x.fav_budget)}">${dkP(x.var_budget_pct)}</td>`;
  const line = x => `<tr class="${x.material ? 'dk-mat' : ''}"><td>${esc(x.label)}${slideNo[x.label] ? `<span class="dk-sn">slide ${slideNo[x.label]}</span>` : ''}</td>${cells(x)}</tr>`;
  const total = (x, label, cls) => x ? `<tr class="${cls}"><td>${esc(x.label || label)}</td>${cells(x)}</tr>` : '';
  const nMat = rows.filter(x => x.material).length;
  out.push({ id: 'pnl',
    title: tot.revenue && tot.opex ? `Revenue ${dkK(tot.revenue.actual_cents)} against operating expenses of ${dkK(tot.opex.actual_cents)}: ${num(nMat)} of ${num(rows.length)} lines moved materially` : 'P&L against prior month and budget',
    rail: 'P&L vs prior month and budget',
    sub: `${d.period_label} actual (AC) against ${d.prior_label} (PM) and budget (BU), $ thousands. Bold lines are material.`,
    body: `<div class="dk-pnl"><table class="dk-tbl"><colgroup><col><col style="width:64px"><col style="width:64px"><col style="width:100px"><col style="width:74px"><col style="width:64px"><col style="width:100px"><col style="width:74px"></colgroup>
      <thead><tr><th scope="col">$ thousands</th><th scope="col">AC</th><th scope="col">PM</th><th scope="col">&Delta;PM</th><th scope="col">%</th><th scope="col">BU</th><th scope="col">&Delta;BU</th><th scope="col">%</th></tr></thead>
      <tbody>${rows.filter(x => x.type === 'revenue').map(line).join('')}${total(tot.revenue, 'Total revenue', 'dk-tot')}
      ${rows.filter(x => x.type !== 'revenue').map(line).join('')}${total(tot.opex, 'Total operating expenses', 'dk-tot')}${total(tot.result, 'Operating result', 'dk-res')}</tbody></table>
      <div><div class="dk-ct">Variance to budget <span>by line, $ thousands</span></div><canvas id="dk-c-var" width="380" height="440" role="img" aria-label="Variance to budget by line. The same figures are in the table."></canvas>
      <p class="dk-note">Green is favourable and red is unfavourable, by business impact.</p></div></div>` });

  /* 4. operating expense bridge */
  const b = d.bridge;
  if (b && b.start && b.booked) {
    const big = (b.steps || []).reduce((m, s) => !m || Math.abs(s.delta_cents) > Math.abs(m.delta_cents) ? s : m, null);
    const hasAdj = (b.adjustments || []).length > 0 && b.end;
    out.push({ id: 'bridge',
      title: `Operating expenses moved from ${dkK(b.start.value_cents)} to ${dkK(b.booked.value_cents)} booked${hasAdj ? `, ${dkK(b.end.value_cents)} after review decisions` : ''}`,
      rail: 'Operating expense bridge',
      sub: big ? `Largest step: ${big.label}, ${dkKs(big.delta_cents)} vs ${d.prior_label}.` : `${d.prior_label} to ${d.period_label}.`,
      body: `<div class="dk-ct">Operating expense bridge <span>${esc(d.prior_label)} to ${esc(d.period_label)}, $ thousands</span></div>
        <canvas id="dk-c-bridge" width="1184" height="440" role="img" aria-label="Waterfall from prior month operating expenses to this month, by category."></canvas>
        <div class="dk-legend"><span><i style="background:${DK.ac}"></i>Totals</span><span><i style="background:${DK.unfav}"></i>Adds cost, unfavourable</span><span><i style="background:${DK.fav}"></i>Reduces cost, favourable</span>
        <span>${hasAdj ? 'Adjustments are review decisions (cancelled entries, refund requests, prepayments). Ledger amounts are unchanged.' : 'No review adjustments so far, so booked is the closing figure.'}</span></div>` });
  }

  /* 5+. one slide per insight */
  ins.forEach(i => {
    const f = i.facts || {}, pr = rowBy[i.category] || {}, cm = i.commentary || {}, nar = i.narrative || {};
    const pinned = !!(cm.pinned && cm.text), isLine = i.chart && (i.chart.type === 'line' || (i.chart.labels || []).length > 3) && i.series13 && (i.series13.labels || []).length > 1;   /* the engine's time-series categories get the trend chart, whatever chart type the AI suggested for the Insights tab */
    const items = (f.review_items || []).slice(0, 4);
    const drv = { headcount: 'Headcount', customers: 'Customers' }[f.driver] || (f.driver ? String(f.driver) : '');
    const com = pinned
      ? `<div class="dk-com dk-pin"><div><div class="dk-lbl">Commentary${cm.stale ? '<span class="dk-st dk-warn">Out of date: written before the latest figures</span>' : ''}</div>
          <p class="dk-txt">${esc(cm.text)}</p><p class="dk-by">${esc(cm.author || 'Author not recorded')}</p></div></div>`
      : `<div class="dk-com"><div class="dk-pend"><strong>Commentary pending</strong>No signed commentary is pinned for this variance yet.</div>
          <div><div class="dk-lbl">${nar.origin === 'ai' ? 'Preliminary note, AI, not reviewed' : 'Code template'}${nar.stale ? `<span class="dk-st dk-warn">${nar.origin === 'ai' ? 'Out of date: figures changed, regenerate in Insights' : 'The AI note is out of date: regenerate it in Insights'}</span>` : ''}</div>
          <p class="dk-txt">${esc([nar.what, nar.context, nar.why].filter(Boolean).join(' ') || 'No note available.')}</p></div></div>`;
    out.push({ id: i.id, title: i.headline || i.title, dot: pinned ? (cm.stale ? 'dk-stale' : 'dk-pinned') : (nar.stale ? 'dk-stale' : ''), insight: i, isLine,
      sub: `${i.category}: ${d.period_label} against ${d.prior_label} and budget`,
      body: `<div class="dk-ins">
        <div><div class="dk-ct">${esc(i.category)} <span>${isLine ? '13 months, budget dashed' : 'prior month, budget, actual'}, $ thousands</span></div>
          <canvas id="dk-c-${esc(i.id)}" width="720" height="380" role="img" aria-label="${esc(i.category)}: ${isLine ? '13 month trend against budget' : 'prior month, budget and actual'}. Figures are in the panel beside the chart."></canvas></div>
        <div class="dk-strip">
          ${dkRow('Reported', dkK(f.current_cents), 'dk-big')}
          ${f.adjusted_cents != null ? dkRow('Adjusted, after review decisions', `${dkK(f.adjusted_cents)}${f.adjusted_pct_vs_prior != null ? ` <small>${dkP(f.adjusted_pct_vs_prior)} vs PM</small>` : ''}`) : ''}
          ${dkRow(`ΔPM, vs ${pmL}`, `<span class="${dkCls(pr.fav_prior)}">${dkKs(f.variance_prior_cents)} &nbsp;${dkP(f.pct_vs_prior)}</span> ${dkTag(pr.fav_prior)}`)}
          ${f.variance_budget_cents != null ? dkRow('ΔBU, vs budget', `<span class="${dkCls(pr.fav_budget)}">${dkKs(f.variance_budget_cents)} &nbsp;${dkP(f.pct_vs_budget)}</span> ${dkTag(pr.fav_budget)}`) : ''}
          ${f.driver_current != null ? dkRow(drv, `${num(f.driver_prior)} to ${num(f.driver_current)} <small>${dkP(f.driver_pct)}</small>`) : ''}
          ${f.volume_effect_cents != null ? dkRow('Volume / rate', `${dkKs(f.volume_effect_cents)} / ${dkKs(f.rate_effect_cents)}`) : ''}
          <div class="dk-items"><div class="dk-lbl">Transactions under review, full dollars</div>
          ${items.map(t => `<div class="dk-item"><span>${esc(t.vendor)}</span>${t.state === 'cancelled' ? `<s>${dkD(t.amount_cents)}</s>` : `<span>${dkD(t.amount_cents)}</span>`}${dkState(t.state, t)}</div>`).join('') || '<p class="dk-none-yet">None flagged in this category.</p>'}
          ${f.review_open_count ? `<p class="dk-note">${num(f.review_open_count)} still open, ${dkD(f.review_open_cents)}.</p>` : ''}</div>
        </div>${com}</div>` });
  });

  /* review and controls */
  const li = (h, amt, sub, note) => `<div class="dk-li"><div class="dk-li-h"><span>${h}</span><span>${amt}</span></div><div class="dk-note">${sub}</div>${note ? `<p>${esc(note)}</p>` : ''}</div>`;
  const none = t => `<p class="dk-none-yet">${t}</p>`;
  const cancelled = adj.filter(a => a.type === 'cancelled'), others = adj.filter(a => a.type !== 'cancelled');
  const cnt = (l, k) => c[k] != null ? dkRow(l, num(c[k])) : '';
  out.push({ id: 'review',
    title: c.total != null ? `${num(c.approved)} of ${num(c.total)} transactions approved, ${num(c.pending)} pending, ${num(open.length)} action item${open.length === 1 ? '' : 's'} open` : 'Review and controls',
    rail: 'Review and controls',
    sub: `Review decisions for ${d.period_label}. Amounts on this slide are full dollars.`,
    body: `<div class="dk-cols">
      <div class="dk-colx"><h3>Reviewed</h3>${cnt('Transactions', 'total')}${cnt('Approved', 'approved')}${cnt('In a checked batch', 'batch_approved')}${cnt('One by one', 'individual')}${c.shortcut ? cnt('Demo shortcut', 'shortcut') : ''}
        ${cnt('Cancelled', 'cancelled')}${cnt('Pending', 'pending')}${cnt('Routed', 'routed')}${cnt('Flagged by rules', 'flagged')}${cnt('Flagged, still open', 'flagged_open')}${cnt('Screened by Jev (System 1)', 'jev_screened')}${cnt('Held by the Jev screen', 'jev_held')}
        ${(d.kept_out || []).length ? `<p class="dk-note" style="margin-top:8px">${num(d.kept_out.length)} variance${d.kept_out.length === 1 ? '' : 's'} kept out of the deck as not material.</p>` : ''}</div>
      <div class="dk-colx"><h3>Open action items</h3>${open.slice(0, 5).map(x => li(esc(x.vendor), dkD(x.amount_cents),
        `${esc(DK_ADJ[x.type] || x.type)}${x.recover_cents != null ? `, expected recovery ${dkD(x.recover_cents)}` : ''}. ${esc(x.owner_name || '')} ${dkState(x.status)}`, x.reply || x.note)).join('') || none('None open.')}
        ${open.length > 5 ? `<p class="dk-note">${num(open.length - 5)} more in the Inbox.</p>` : ''}</div>
      <div class="dk-colx"><h3>Cancelled entries</h3>${cancelled.slice(0, 5).map(x => li(esc(x.vendor), `<s>${dkD(x.amount_cents)}</s>`,
        `<span class="dk-mono">${esc(x.txn_id)}</span> ${esc(x.category || '')}${x.by_name ? ', by ' + esc(x.by_name) : ''}`, x.note)).join('') || none('No entries cancelled.')}</div>
      <div class="dk-colx"><h3>Adjustments</h3>${others.slice(0, 4).map(x => li(esc(x.vendor), dkD(x.effect_cents),
        `${esc(DK_ADJ[x.type] || x.type)} on ${dkD(x.amount_cents)}${x.by_name ? ', by ' + esc(x.by_name) : ''}`, x.note)).join('') || none('No refund requests or prepayments.')}
        ${dkRow('Net effect of all review decisions', dkD(c.adjustment_total_cents || 0))}
        <p class="dk-note" style="margin-top:8px">Ledger amounts are unchanged. Adjusted figures include expected recoveries that have not been received yet.</p></div></div>` });

  /* integrity */
  out.push({ id: 'integrity',
    title: cl ? `Close package anchored with fingerprint ${cl.hash8}` : 'Draft, not anchored: there is no fingerprint yet',
    rail: 'Integrity',
    sub: cl ? `Signed by ${cl.closed_by_name}. Verify it on the Anchor tab.` : 'The fingerprint is written when the close is finalized on the Anchor tab.',
    body: `<div class="dk-integ"><div><div class="dk-lbl">SHA-256 of the canonical close package</div>
        <div class="dk-hash dk-mono">${cl ? esc(cl.hash) : 'Draft, not anchored'}</div>
        ${cl ? dkRow('Close id', `<span class="dk-mono">${esc(cl.close_id)}</span>`) + dkRow('Previous fingerprint', `<span class="dk-mono">${esc(cl.prev_hash8 || 'none')}</span>`) + dkRow('Topic id', `<span class="dk-mono">${esc(cl.topic_id || 'none')}</span>`)
          + dkRow('Anchored at', esc(String(cl.ts || '').replace('T', ' ').slice(0, 16))) + dkRow('Signed by', esc(cl.closed_by_name)) + dkRow('Tier', esc(cl.tier))
          : dkRow('Status', 'Draft') + dkRow('Blocking the close', `${num(c.pending || 0)} pending transaction${c.pending === 1 ? '' : 's'}`)}</div>
      <div><div class="dk-lbl">What is hashed</div><ul class="dk-ul"><li>Every transaction of the period with its status, approver, note, resolution, rule flags and Jev score</li><li>Routes on those transactions, with replies</li><li>Pinned commentary and the facts of its insight</li></ul>
        <div class="dk-lbl" style="margin-top:18px">What is not hashed</div><ul class="dk-ul"><li>This deck preview</li><li>Preliminary AI notes and unpinned drafts</li></ul></div></div>` });
  return out;
}

/* ---- view ---- */
function dkFit() {
  const st = $('.dk-stage'); if (!st) return;
  const w = st.clientWidth, h = st.clientHeight, k = Math.min(w / 1280, h / 720);
  st.style.setProperty('--dk-k', k); st.style.setProperty('--dk-x', (w - 1280 * k) / 2 + 'px'); st.style.setProperty('--dk-y', (h - 720 * k) / 2 + 'px');
}
function dkGo(i) {
  if (!dkIds.length) return;
  dkI = Math.max(0, Math.min(dkIds.length - 1, i)); S.deckSlide = dkIds[dkI];
  $$('.dk-slide').forEach((el, k) => el.classList.toggle('dk-on', k === dkI));
  $$('.dk-ri').forEach((el, k) => { el.setAttribute('aria-current', k === dkI ? 'true' : 'false'); if (k === dkI && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' }); });
  const n = $('#dk-count'); if (n) n.textContent = `${dkI + 1} / ${dkIds.length}`;
  const p = $('#dk-prev'), x = $('#dk-next'); if (p) p.disabled = dkI === 0; if (x) x.disabled = dkI === dkIds.length - 1;
  try { history.replaceState(null, '', '#/deck?slide=' + encodeURIComponent(S.deckSlide)); } catch (_) { /* file: or sandboxed */ }
}
function dkPresent() {
  const r = $('.dk-root'); if (!r) return;
  if (document.fullscreenElement) document.exitFullscreen();
  else if (r.requestFullscreen) r.requestFullscreen().catch(() => flash('This browser did not allow full screen.', 'warn'));
}
function dkKey(e) {
  if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || document.querySelector('dialog[open]')) return;
  const step = { ArrowRight: 1, PageDown: 1, ArrowLeft: -1, PageUp: -1 }[e.key];
  if (step) { e.preventDefault(); dkGo(dkI + step); }
  else if (e.key === 'Home') { e.preventDefault(); dkGo(0); }
  else if (e.key === 'End') { e.preventDefault(); dkGo(dkIds.length - 1); }
  else if (e.key === 'p' || e.key === 'P') { e.preventDefault(); dkPresent(); }
}

async function deckView() {
  const main = $('#main');
  main.innerHTML = '<p class="muted">Loading the deck.</p>';
  const d = await get('/api/deck');
  if (S.tab !== 'deck' || !$('#main')) return;
  const slides = dkSlides(d), N = slides.length, fin = d.status === 'final';
  const foot = n => `<footer class="dk-foot"><span>${esc(d.company)} · ${esc(d.period_label)} close · ${fin ? 'Final, signed' : 'Draft, not signed'} · $ thousands unless stated</span>
    <span>${d.synthetic ? 'Synthetic demo data' : ''}</span><span>${d.close ? 'sha256 ' + esc(d.close.hash8) : 'no fingerprint yet'} · ${n} / ${N}</span></footer>`;
  main.innerHTML = `<div class="dk-root">
    <nav class="dk-rail" aria-label="Slides"><div class="dk-rail-h">${fin ? 'Final deck' : 'Draft deck'}, ${N} slides</div>
      ${slides.map((s, k) => `<button type="button" class="dk-ri" data-go="${k}"><span class="dk-ri-n">${k + 1}</span><span class="dk-ri-t">${esc(s.rail || s.title)}</span><span class="dk-dot ${s.insight ? (s.dot || '') : 'dk-none'}"></span></button>`).join('')}
      <div class="dk-key"><span><span class="dk-dot dk-pinned"></span>Commentary pinned</span><span><span class="dk-dot"></span>Commentary pending</span><span><span class="dk-dot dk-stale"></span>Out of date</span></div></nav>
    <div class="dk-col"><div class="dk-chrome">
      <button type="button" class="btn" id="dk-prev">Previous</button><span class="dk-count" id="dk-count" aria-live="polite"></span><button type="button" class="btn" id="dk-next">Next</button>
      <button type="button" class="btn" id="dk-present" title="Full screen. Press P to start, Esc to leave.">Present</button>
      <button type="button" class="btn primary" id="dk-print" title="Opens the print dialog with one slide per page.">Export PDF</button>
      <span class="dk-hint">Chrome: Save as PDF, margins None, background graphics on. Safari ignores the page size.</span></div>
      <div class="dk-stage">${slides.map((s, k) => `<section class="dk-slide ${s.cls || ''}" aria-label="Slide ${k + 1} of ${N}: ${esc(s.rail || s.title)}">
        <header class="dk-head">${s.head != null ? s.head : `<h2 class="dk-title">${esc(s.title)}</h2><p class="dk-sub">${esc(s.sub || '')}</p>`}</header>
        <div class="dk-body">${s.body}</div>${foot(k + 1)}</section>`).join('')}</div></div></div>`;

  /* charts: drawn once at fixed size; hidden slides keep their size, so nothing is redrawn on navigation */
  if (d.pnl && d.pnl.rows) dkChartVariance('dk-c-var', d.pnl.rows);
  if (d.bridge && d.bridge.start && d.bridge.booked) dkChartBridge('dk-c-bridge', d.bridge);
  const tl = (d.trend && d.trend.labels) || [], acL = dkMon(d.period), pmL = tl.length > 1 ? dkMon(tl[tl.length - 2]) : 'Prior';
  slides.filter(s => s.insight).forEach(s => s.isLine ? dkChartTrend('dk-c-' + s.id, s.insight.series13) : dkChartTriplet('dk-c-' + s.id, s.insight.facts || {}, pmL, acL));

  /* wiring: listeners live inside the root, plus three on document and window that deckUnmount removes */
  const root = $('.dk-root');
  root.addEventListener('click', e => {
    const go = e.target.closest('[data-go]');
    if (go) return dkGo(+go.dataset.go);
    const id = (e.target.closest('button') || {}).id;
    if (id === 'dk-prev') dkGo(dkI - 1); else if (id === 'dk-next') dkGo(dkI + 1);
    else if (id === 'dk-present') dkPresent(); else if (id === 'dk-print') window.print();
    else if (document.fullscreenElement === root && e.target.closest('.dk-stage')) dkGo(dkI + 1);
  });
  dkIds = slides.map(s => s.id);
  document.documentElement.classList.add('dk-active');
  if (!document.getElementById('dk-page')) document.head.insertAdjacentHTML('beforeend', '<style id="dk-page">@page { size: 13.333in 7.5in; margin: 0; }</style>');
  document.addEventListener('keydown', dkKey); document.addEventListener('fullscreenchange', dkFit); addEventListener('resize', dkFit);
  dkOn = true;
  const want = (location.hash.match(/[?&]slide=([^&]+)/) || [])[1];
  const at = dkIds.indexOf(want ? decodeURIComponent(want) : S.deckSlide);
  dkFit(); dkGo(at < 0 ? 0 : at);
}

function deckUnmount() {
  dkCharts.forEach(c => c.destroy()); dkCharts = [];
  if (!dkOn) return;
  dkOn = false; dkIds = [];
  document.removeEventListener('keydown', dkKey); document.removeEventListener('fullscreenchange', dkFit); removeEventListener('resize', dkFit);
  document.documentElement.classList.remove('dk-active');
  const st = document.getElementById('dk-page'); if (st) st.remove();
  if (document.fullscreenElement && document.fullscreenElement.classList.contains('dk-root')) document.exitFullscreen();
}
