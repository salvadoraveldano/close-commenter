# Close Commenter: build contract (delete or fold into README at freeze)

Spec: `hackaton_final_idea.md`. Synthetic data only. Never print or log secret values. Python stdlib only for the app (no pip installs). Money = integer cents everywhere.

## Constants
- Company "Northgate Labs" (invented). Closing period `2026-09`; history `2025-09`..`2026-08` (all approved). Txn id format `YYMM-NNNN` (e.g. `2609-0217`). Planted ids are fixed (see seed): duplicate invoice = `2609-0217`; tamper demo target = `2609-0231` (vendor known, amount exactly 498000 cents, unflagged).
- Users (password = `demo-<role>`, shown on login screen): `u_preparer` Alex Kim / preparer, `u_controller` Chava / controller, `u_cfo` Dana Whitfield / cfo, `u_auditor` Omar Reyes / auditor, `u_owner` Priya Nair / owner. Only controller and cfo may approve; a user may not approve a txn they prepared (segregation of duties). Anyone may route and reply.
- Approval limit 500000 cents ($5,000). Batch materiality cap 2500000 per txn. Vendor needs >= 3 prior periods. Within +/-25% of vendor median. JEV gate: holds when `fraud >= 0.5` (confidence is shown, not gated: half of routine txns score under 0.5; if a txn has no jev, the gate skips JEV and says so).
- Categories (type): revenue = Subscription Revenue, Services Revenue; expense = Payroll & Benefits, Cloud Hosting, Software & SaaS, Marketing, Travel & Entertainment, Office & Admin, Professional Fees, Rent & Occupancy, Insurance, Depreciation, Contractors. `kind` = `activity` (source bank|ap|card|payroll) or `accounting` (source accrual|reversal|depr|reclass|je).
- Flag codes (exact): `duplicate`, `new_vendor`, `split_payment`, `weekend_je`, `round_amount`, `near_dup_vendor`, `accrual_no_reversal`, `outlier`. 8 planted cases, one per code, answer key in `meta` key `planted` = JSON `{code: [txn ids]}`. `risk` 0-100; label high >= 60, med >= 30, else low.

## SQLite schema (`close.db`)
```sql
users(id TEXT PRIMARY KEY, name TEXT, role TEXT, password TEXT, hint TEXT)
txns(id TEXT PRIMARY KEY, date TEXT, period TEXT, vendor TEXT, category TEXT, account TEXT, amount_cents INTEGER,
     kind TEXT, source TEXT, preparer TEXT, approver TEXT, approve_note TEXT, approve_ts TEXT, memo TEXT,
     source_ref TEXT, status TEXT DEFAULT 'pending', flags TEXT DEFAULT '[]', risk INTEGER DEFAULT 0, jev TEXT)
drivers(period TEXT PRIMARY KEY, headcount INTEGER, occupancy_pct REAL, customers INTEGER)
budget(period TEXT, category TEXT, amount_cents INTEGER, PRIMARY KEY(period, category))
routes(id INTEGER PRIMARY KEY AUTOINCREMENT, txn_ids TEXT, from_user TEXT, to_user TEXT, reason TEXT, note TEXT,
       status TEXT DEFAULT 'open', reply TEXT, ts TEXT, reply_ts TEXT)
commentary(insight_id TEXT PRIMARY KEY, user_id TEXT, draft TEXT, enhanced TEXT, final TEXT, pinned INTEGER DEFAULT 0, ts TEXT)
audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user_id TEXT, action TEXT, target TEXT, detail TEXT)
closes(close_id TEXT PRIMARY KEY, period TEXT, hash TEXT, prev_hash TEXT, topic_id TEXT, messages INTEGER, bytes INTEGER,
       tier TEXT, links TEXT, closed_by TEXT, ts TEXT)
meta(key TEXT PRIMARY KEY, value TEXT)
```
`txns.flags` = JSON list of `{"code","msg"}`. `txns.jev` = JSON `{"fraud":0-1,"confidence":0-1,"risk_type":str,"unusualness":0-1,"latency_ms":int,"origin":"cached"|"live"}` or NULL. `status` = pending|approved|routed. Timestamps ISO-8601 UTC strings. Cached AI material lives in `cache/jev.json` (`{txn_id: jev}`), `cache/insights.json` (`{insight_id: {narrative, chart}}`), `cache/enhance_recorded.json` (`{insight_id: {draft_hint, enhanced}}`); seed loads them if present.

## Python module interfaces
`seed.py`: `build(path="close.db") -> None` (drops and recreates everything, deterministic RNG seed 42, loads cache files, runs `engine.flag_period`); CLI `python3 seed.py` builds; `python3 seed.py --ai` additionally calls `jev.screen` for the closing period and `llm.narrate`, rewriting the cache files.

`engine.py` (pure; takes an open sqlite3 connection with `row_factory=sqlite3.Row`):
- `PERIOD="2026-09"`, `risk_label(risk)->str`
- `flag_period(conn, period)` writes `flags` and `risk` for every txn of the period (idempotent)
- `vendor_stats(conn, txn_id)` -> `{first_seen, periods_seen, median_cents, history:[{period, amount_cents}]}`; `category_stats(conn, txn_id)` -> `{median_cents, current_total_cents, prior_total_cents, multiple_vs_median}`
- `batch_check(conn, ids=None, user_id=None)` -> `{"eligible":[id], "held":[{"id","reasons":[str],"codes":[str]}], "thresholds":{...}}` (ids None = all pending in closing period). Codes include flag codes plus `over_cap`, `young_vendor`, `off_history`, `jev`, `sod`, `not_pending`.
- `insights(conn, period)` -> `{"insights":[{"id","title","category","facts":{...numbers only...},"explanation":"volume|rate|mixed|unexplained","chart":{"type":"bar"|"line","title","labels":[],"series":[{"name","values":[cents]}]}}], "kept_out":[{"title","reason","amount_cents"}]}`. Facts include current/prior/budget/avg3 cents, pct changes, driver name + current/prior/pct change, volume_effect_cents, rate_effect_cents, per_driver current/prior. Materiality: abs variance >= 1500000 cents AND >= 10%. Ids stable, e.g. `ins-office-admin`.
- `template_narrative(insight)` -> `{"what","context","why"}` deterministic text built only from facts.
- `number_guard(text, allowed_numbers, user_text="")` -> `{"numbers":[str],"unverified":[str],"marked_html":str}`; normalises `$`, commas, `%`, k/M, "x"; a number is verified if it matches (within 0.5%) a number in `allowed_numbers` (iterable of floats derived from facts, including cents/100, percents, and pct as fraction) or appears in `user_text`; `marked_html` wraps unverified figures in `<mark class="unverified">` and HTML-escapes the rest.
- `jev_state(conn, txn_id)` -> dict sent to JEV as `state`: `{txn:{vendor,category,amount_cents,date,kind,source,memo,preparer}, vendor_history:{periods_seen,median_cents,first_seen}, category:{median_cents,multiple_vs_median}, code_flags:[codes]}`; `facts_numbers(facts)` -> `set[float]` of every number in an insight's facts, plus each cents value /100 and each pct value in both percent and fraction form (feeds `number_guard`).
- `build_package(conn, period)` -> canonical bytes (v1): `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)` UTF-8, obj = `{v:1, company, period, source_system:"QuickBooks Online (synthetic export)", txns:[{id,date,vendor,category,account,amount_cents,kind,source,status,preparer,approver,approve_note,approve_ts,memo,source_ref,flags:[codes],jev:{fraud,confidence,risk_type}|null}] sorted by id, routes:[{id,txn_ids,from_user,to_user,reason,note,status,reply}] for that period, insights:[{id,title,facts,commentary_final,pinned}] (pinned only), drivers:[...], budget:[...]}`. No timestamps of generation, no audit rows, nothing that changes on verify.
- `package_hash(b)` -> hex sha256; `diff_packages(old, new)` -> `[{"id","field","was","now"}]` (missing/extra txn = field `__row__`).

`jev.py`: `available()->bool`; `screen(states:{txn_id: dict}) -> {txn_id: jev dict (schema above)}`; one HTTP call per txn, 8 threads, never raises (missing key or HTTP error -> omitted from result). `llm.py`: `provider()->str` (env `LLM`, default `anthropic` if `ANTHROPIC_API_KEY` set else `off`); `narrate(insights)->{insight_id:{what,context,why,chart_type}}` (facts in, prose out, every figure must be in facts; validated with `engine.number_guard`, failures fall back to template); `enhance(draft, facts)->str`; both return template/None on failure. `chain.mjs` CLI (JSON on stdout, testnet only, reads env `HEDERA_ACCOUNT_ID`, `HEDERA_PRIVATE_KEY`): `init`, `publish --package F --close-id ID --period P --hash H --prev H|"" --closed-by U [--hash-only] --out result.json`, `fetch --topic T --close-id ID --out F`.

## HTTP API (app.py, `127.0.0.1:8000`, JSON, cookie `sid`, errors `{"error":str, ...}` with 400/401/403/404/409)
- `POST /api/login {user_id,password}` -> `{user:{id,name,role}}`; `POST /api/logout`; `GET /api/users` -> `[{id,name,role,hint,demo_password}]` (public, for login screen); `GET /api/me` -> `{user,period,company,demo:true,ai:{system2:str,jev:bool},chain:{configured:bool,topic_id:str|null}}`
- `GET /api/summary` -> `{period,total,approved,routed,pending,flagged,flagged_pending,at_risk_cents,by_kind:{activity,accounting},readiness_pct,finalized}`
- `GET /api/txns?kind=&status=&risk=high|med|low&category=&q=&limit=500&offset=0` -> `{rows:[Txn],total}`; `Txn` = `{id,date,vendor,category,amount_cents,kind,source,status,risk,risk_label,flags:[{code,msg}],jev,preparer,approver,approve_note,memo,source_ref}`
- `GET /api/txns/<id>` -> `{txn,vendor:<vendor_stats>,category:<category_stats>,routes:[Route],audit:[{ts,user_id,action,detail}]}`
- `POST /api/approve {ids:[one id],note}` (note >= 5 chars required when risk_label != low) -> `{approved:[ids],refused:[{id,reasons}]}`; 403 if role cannot approve or segregation of duties fails; 409 if period finalized.
- `POST /api/batch-check {ids?}` -> `engine.batch_check` result; `POST /api/batch-approve {ids}` -> `{approved,refused:[{id,reasons,codes}]}`; the server re-runs the gate; if nothing approved and something refused -> 403 with the same body plus `error`.
- `POST /api/route {ids,to_user,reason:second_opinion|investigation|clarification|approval,note}` -> `{route}`; `GET /api/inbox` -> `{open:[Route],sent:[Route]}` for the current user; `POST /api/route/<id>/reply {text,resolve}` -> `{route}`. `Route` = `{id,txn_ids,from_user,from_name,to_user,to_name,reason,note,status,reply,ts,reply_ts,txns:[{id,vendor,amount_cents}]}`. Routing sets txn status `routed`; resolving sets it back to `pending`.
- `GET /api/insights` -> `{insights:[Insight],kept_out}`; `Insight` = engine insight + `{narrative:{what,context,why,origin:"ai"|"template"},chart:{...,origin},commentary:{draft,enhanced,final,pinned,words,can_enhance,unverified:[str]}}`. `POST /api/commentary {insight_id,draft}` -> `{words,can_enhance}` (can_enhance = >= 25 words). `POST /api/enhance {insight_id,mode:"live"|"recorded"}` -> `{enhanced,marked_html,unverified:[str],origin:"live"|"recorded"|"template"}`; 403 `{error}` when under 25 words (also for recorded). `POST /api/pin {insight_id,pinned,use:"draft"|"enhanced"}` -> `{ok}` (sets `final`). `GET /pack.html` -> printable HTML of pinned insights.
- `GET /api/audit?limit=100` -> `[{ts,user_id,action,target,detail}]`
- Chain: `POST /api/close/finalize` (controller or cfo; blockers: pending > 0, no pinned insight with own words, already finalized -> 409 `{error,blockers:[str]}`) -> `{close:{close_id,period,hash,hash8,prev_hash8,topic_id,messages,bytes,tier:"full"|"hash-only",links:[{label,url}],closed_by,ts}}`; `GET /api/close/status` -> `{finalized,close|null,history:[close]}`; `POST /api/close/verify` -> `{ok,live_hash8,chain_hash8,changed:[{id,field,was,now}],ms,via:"mirror-node"}`; `POST /api/close/restore` -> `{ok,hash_ok,txns,bytes,files:{json:"/restored/2026-09.json",csv:"/restored/2026-09.csv"}}`; `POST /api/demo/tamper` -> `{id,field:"amount_cents",was,now}` (sets `2609-0231` amount x10 directly in DB, audit action `external_edit`); `POST /api/reset` -> `{ok}` (rebuilds seed, keeps `closes` rows of earlier periods, deletes current-period close).
- Static: `/` -> `static/index.html`; `/static/*`; `/restored/*`.

## File ownership (disjoint)
A: `seed.py`, `engine.py`, `check.py`, `cache/enhance_recorded.json`. B: `jev.py`, `llm.py`, later `app.py`. C: `static/index.html`, `static/app.js`, `static/style.css`, `static/vendor/*`. D: `chain.mjs`, `package.json`, `package-lock.json`, `chain.json`. Lead (me): `API.md`, `README.md`, `.gitignore`, `.env.op`, integration.

## Changes after the first build (current behaviour)
- `POST /api/logout` exists and ends the session. `POST /api/demo/complete-review` (controller/cfo) approves everything open and resolves open routes, audit action `demo_shortcut`.
- Writers: commentary, enhance and pin need role controller, cfo or preparer. Reset, tamper and the shortcut need controller or cfo. `/pack.html` and `/restored/*` need a session.
- `POST /api/commentary` returns `{words, can_enhance, unpinned, changed}`; a changed draft unpins and clears the stored enhancement. Words are tokens with a letter or digit (`\w+`). `POST /api/pin` with `use:"enhanced"` answers 409 `{unverified:[...]}` if any figure is unverified.
- `POST /api/close/verify` also returns `record_ok` (local closes row vs the chain `begin` message). `diff_packages` reports non-transaction sections as `{id:<section>, field:"__section__"}`.
- Route replies and every other mutation answer 409 once the period is final or while the Hedera write is running. Bad bodies answer 400, oversized 413.

## v2 contract (review decisions, card review, deck). This section wins over anything above it.
Owners: A = `engine.py app.py seed.py llm.py check.py`; B = `static/app.js static/style.css static/index.html`; C = `static/cards.js static/cards.css`; D = `static/deck.js static/deck.css`. Nobody edits a file they do not own. Client code does no arithmetic on money: every figure comes from the server.

### Data
- `txns.resolution TEXT` = JSON or NULL: `{"code":"supported"|"prepayment"|"one_time"|"timing"|"refund_request","months":int?,"recover_cents":int?}`. `commentary.facts_hash TEXT`. `status` = `pending|approved|routed|cancelled`.
- Payments desk = `u_preparer` (Alex Kim), `engine.PAYMENTS_USER`; its hint text becomes "AP / Payments desk: prepares entries and recovers overpayments. Cannot approve." No new user.
- The ledger amounts are never changed. `effect_cents` of a decision (0 when none): cancelled = `-amount_cents`; refund_request = `-recover_cents`; prepayment = `-(amount_cents - amount_cents // months)`. `adjusted = booked + sum(effects)`.
- "Cash expense" = `kind == "activity"` and `engine.CATEGORIES[category] == "expense"`.
- AI narratives: `meta` key `nar:<insight_id>` = JSON `{facts_hash, what, context, why, chart_type}`; `cache/insights.json` entries gain `facts_hash`. `engine.facts_hash(insight)` = first 16 hex of sha256 over `json.dumps({title, category, explanation, facts}, sort_keys=True)`. A narrative is served as AI only when its hash equals the current one.

### Endpoints (all need a session; 403 for role/kind/segregation, 409 when not pending or the period is final, 400 for bad input; errors are `{"error": str}`)
- `Txn` gains `resolution` (object|null), `effect_cents` (int), `state` = `pending|routed|approved|cancelled|refund_requested`. `GET /api/txns` accepts `status=cancelled` and `queue=held` (pending items the batch gate holds for the current user, highest risk first; each row also carries `held_reasons:[str]`).
- `GET /api/txns/<id>` unchanged plus the new Txn fields.
- `POST /api/approve {ids:[id], note, resolution?}`: `resolution = {code: supported|prepayment|one_time|timing, months?}`. `prepayment` needs integer `months` 2..60 and a cash expense. Note rule unchanged (5+ chars when risk is not low). -> `{approved, refused}`.
- `POST /api/cancel {id, note}`: controller/cfo; `kind == "accounting"` (else 403 "Only accounting-only entries can be cancelled. For a cash transaction, request a refund."); status pending; note 5+ chars. Sets status `cancelled`, approver, approve_note, approve_ts; audit `cancel`. -> `{txn: Txn}`.
- `POST /api/action {id, type:"refund_request", recover_cents, note}`: controller/cfo; cash expense; pending; not prepared by the caller; integer `0 < recover_cents <= amount_cents`; note 5+ chars. Approves with `resolution {code:"refund_request", recover_cents}` and inserts a route `{reason:"refund_request", to_user: PAYMENTS_USER, note}` that stays open without changing the txn status; audit `refund_request`. -> `{txn: Txn, route: Route}`.
- `POST /api/reopen {id}`: controller/cfo; status approved or cancelled; -> pending, clears approver, note, ts, resolution; marks its open refund routes `withdrawn`; audit `reopen`. -> `{txn: Txn}`.
- `Route` gains `recover_cents` (int|null) taken from the txn resolution; reason may be `refund_request`.
- `GET /api/summary` gains `cancelled`, `decided` (approved + cancelled), `actions_open` (open refund routes), `adjustment_cents` (sum of all effects in the period); `readiness_pct` uses decided; `flagged_pending` and `at_risk_cents` ignore cancelled.
- `GET /api/insights`: each insight gains `facts_hash`, `headline` (code-written action title), and facts keys `review_items:[{txn_id,vendor,amount_cents,state,resolution,months,recover_cents,effect_cents,note,by_name}]` (flagged or decided-with-effect txns of that category, largest first, max 5), `review_open_count`, `review_open_cents`, `adjustment_cents`, and only when that is not 0: `adjusted_cents`, `adjusted_variance_prior_cents`, `adjusted_pct_vs_prior`, `adjusted_variance_budget_cents`, `adjusted_pct_vs_budget`. `narrative` = `{what, context, why, origin:"ai"|"template", stale:bool}` (stale = an AI summary could be regenerated for the current facts). `commentary` gains `stale:bool` (written against older facts) and `author` (name).
- `POST /api/insights/<id>/regenerate`: controller/cfo/preparer; one Claude call, number guard; stores and returns `{narrative}`; with AI off returns the template with `origin:"template", stale:false`.
- `GET /api/deck` ->
```
{company, period, period_label:"September 2026", prior_label:"August 2026", status:"draft"|"final", units:"USD", synthetic:true, prepared_by:<current user name>,
 close: null | {close_id, hash, hash8, prev_hash8, topic_id, ts, closed_by_name, tier},
 kpis: [ {key:"revenue"|"opex"|"result", label, value_cents, adjusted_cents, prior_cents, budget_cents, var_prior_cents, var_prior_pct, fav_prior, var_budget_cents, var_budget_pct, fav_budget},
         {key:"margin", label:"Operating margin", value_pct, prior_pct, budget_pct, var_prior_pts, fav_prior},
         {key:"headcount"|"customers", label, value, prior, var_prior_pct} ],
 pnl: {rows:[{label, type:"revenue"|"expense", actual_cents, adjustment_cents, adjusted_cents, prior_cents, budget_cents, var_prior_cents, var_prior_pct, fav_prior, var_budget_cents, var_budget_pct, fav_budget, material:bool}],
       totals:{revenue:{...same fields, label}, opex:{...}, result:{...}}},
 trend: {labels:[13 periods "YYYY-MM"], revenue_cents:[], opex_cents:[], result_cents:[], budget_revenue_cents:[], budget_opex_cents:[]},
 bridge: {start:{label, value_cents}, steps:[{label, delta_cents, favorable}], booked:{label, value_cents}, adjustments:[{label, delta_cents, favorable}], end:{label, value_cents}},
 insights: [ {id, title, headline, category, type:"revenue"|"expense", explanation, facts, chart, narrative:{what,context,why,origin,stale}, commentary:{text, author, pinned, stale}, series13:{labels:[], actual_cents:[], budget_cents:[]}} ],
 controls: {total, approved, cancelled, pending, routed, batch_approved, individual, shortcut, flagged, flagged_open, jev_screened, jev_held, adjustment_total_cents,
            adjustments:[{txn_id, vendor, category, type:"cancelled"|"refund_request"|"prepayment", amount_cents, effect_cents, note, by_name}]},
 open_items: [{txn_id, vendor, amount_cents, type:"refund_request"|"route", recover_cents, owner_name, status, note, reply}],
 kept_out: [{title, reason, amount_cents}]}
```
  `fav_*` is by business impact: revenue and result up = favourable; expense up = unfavourable. Opex = all expense categories; result = revenue - opex. `bridge.steps` = the 6 largest opex category deltas vs prior month plus one "Other" step; `start + sum(steps) = booked`; `booked + sum(adjustments) = end`. Percentages are rounded to 1 decimal by the server.
- Finalize blockers: every transaction must be approved or cancelled (open refund requests do not block). `/pack.html` is gone. The close package adds `resolution` per transaction.

### Front-end wiring (already in place in `index.html` and `app.js`)
- Script order: `vendor/chart.umd.js`, `cards.js`, `deck.js`, `app.js`. Styles: `style.css`, `cards.css`, `deck.css`.
- `cards.js` defines globals `cardsMount(root)` (render the card review into `root`, inside `#main`; called when Review is in Cards mode) and `cardsUnmount()` (remove listeners and timers). `deck.js` defines `async deckView()` (renders into `#main`, honours `#/deck?slide=<insight_id>`) and `deckUnmount()` (destroy charts, leave present mode).
- Both files may use these `app.js` globals at call time only (never at load time): `$ $$ esc usd num pct shortDate when get post S can() uname(id) flash(html, cls) busy(btn, label, fn) reduced riskChip(t) rsn(reasons) REASONS`. They attach their own listeners inside their root, never use `data-act`, and prefix every CSS class (`cd-` for cards, `dk-` for deck). `app.js` skips its Review keys while `S.rvMode === 'cards'`.

### After the adversarial pass
- `POST /api/cancel` also answers 403 when the caller prepared the entry (segregation of duties, same as approve and refund). In the seed the controller prepared 9 of the 10 accounting entries, so the controller can cancel only `2609-0261`; the CFO can cancel the rest.
- `POST /api/route/<id>/reply`: a withdrawn route answers 409; on a `refund_request` route only the recipient (Payments desk) may resolve.
- A body that cannot be parsed (including pathological nesting) answers 400.
