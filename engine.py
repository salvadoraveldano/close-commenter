"""Pure rules engine for Close Commenter: flags, batch gate, insights, number guard, canonical close package.
Takes an open sqlite3 connection with row_factory=sqlite3.Row. Money is integer cents. Stdlib only."""
import calendar, difflib, hashlib, html, json, re, statistics
from datetime import date

PERIOD = "2026-09"
COMPANY = "Northgate Labs"
LIMIT = 500000          # approval limit, cents
CAP = 2500000           # batch materiality cap per txn
MIN_PERIODS = 3         # vendor needs >= 3 prior periods
BAND = 0.25             # within +/-25% of vendor median
JEV_FRAUD, JEV_CONF = 0.5, 0.5
MATERIAL = 1500000      # insight materiality: abs variance >= $15,000 AND >= 10%
MATERIAL_PCT = 10.0
OUTLIER_X = 1.6         # outlier: amount >= 1.6x vendor median
NEW_VENDOR_X = 3.0      # new vendor: first payment >= 3x category median
DUP_MIN = 25000         # duplicate rule ignores tickets under $250
DUP_DAYS = 5
NEAR_DUP_RATIO = 0.88
TOP_N_COFFEE = 3

CATEGORIES = {"Subscription Revenue": "revenue", "Services Revenue": "revenue", "Payroll & Benefits": "expense",
              "Cloud Hosting": "expense", "Software & SaaS": "expense", "Marketing": "expense",
              "Travel & Entertainment": "expense", "Office & Admin": "expense", "Professional Fees": "expense",
              "Rent & Occupancy": "expense", "Insurance": "expense", "Depreciation": "expense", "Contractors": "expense"}
CODES = ["duplicate", "new_vendor", "split_payment", "weekend_je", "round_amount", "near_dup_vendor",
         "accrual_no_reversal", "outlier"]
DRIVER = {"Payroll & Benefits": "headcount", "Office & Admin": "headcount", "Software & SaaS": "headcount",
          "Travel & Entertainment": "headcount", "Contractors": "headcount", "Cloud Hosting": "customers",
          "Subscription Revenue": "customers", "Services Revenue": "customers", "Marketing": "customers",
          "Professional Fees": "customers", "Rent & Occupancy": "occupancy_pct", "Insurance": "headcount",
          "Depreciation": "headcount"}
SLUG = {"Office & Admin": "ins-office-admin", "Cloud Hosting": "ins-cloud-hosting", "Subscription Revenue": "ins-revenue",
        "Marketing": "ins-marketing", "Payroll & Benefits": "ins-payroll"}
ORDER = ["Office & Admin", "Cloud Hosting", "Subscription Revenue", "Marketing", "Payroll & Benefits"]
LINE_CATS = ("Cloud Hosting", "Subscription Revenue")
USERS = {"u_preparer": "Alex Kim", "u_controller": "Chava", "u_cfo": "Dana Whitfield", "u_auditor": "Omar Reyes",
         "u_owner": "Priya Nair"}
PAYMENTS_USER = "u_preparer"   # the AP / Payments desk that recovers overpayments
RESOLUTIONS = ("supported", "prepayment", "one_time", "timing", "refund_request")
DECIDED = ("approved", "cancelled")
BASE_RISK = {"duplicate": 88, "split_payment": 78, "weekend_je": 66, "near_dup_vendor": 64, "accrual_no_reversal": 68}


def risk_label(risk):
    return "high" if risk >= 60 else "med" if risk >= 30 else "low"


def resolution(row):
    try:
        v = json.loads(row["resolution"]) if row["resolution"] else None
    except (TypeError, ValueError, KeyError, IndexError):
        v = None
    return v if isinstance(v, dict) else None


def effect_cents(row):
    """What a review decision does to the reported figure. The ledger amount itself is never changed."""
    if row["status"] == "cancelled":
        return -row["amount_cents"]
    r = resolution(row) if row["status"] == "approved" else None
    if not r:
        return 0
    n = lambda v: v if isinstance(v, int) and not isinstance(v, bool) else 0   # a hand-edited resolution must not take every endpoint down
    if r.get("code") == "refund_request":
        return -n(r.get("recover_cents"))
    if r.get("code") == "prepayment" and n(r.get("months")) >= 2:
        return -(row["amount_cents"] - row["amount_cents"] // r["months"])   # one month stays in the period
    return 0


def state(row):
    return "refund_requested" if row["status"] == "approved" and (resolution(row) or {}).get("code") == "refund_request" else row["status"]


def facts_hash(ins):
    return hashlib.sha256(json.dumps({k: ins.get(k) for k in ("title", "category", "explanation", "facts")},
                                     sort_keys=True).encode()).hexdigest()[:16]


def _dir(v):
    return "up" if v >= 0 else "down"


def headline(ins):
    """Code-written action title, built only from facts."""
    f, cat = ins["facts"], ins["category"]
    pp = f["pct_vs_prior"]
    if f.get("adjustment_cents"):
        return (f"{cat} {_dir(f['variance_prior_cents'])} {abs(pp):.1f}% as booked, {_dir(f['adjusted_variance_prior_cents'])} "
                f"{abs(f['adjusted_pct_vs_prior']):.1f}% after review adjustments")   # direction from cents, as the narrative does
    dn = f["driver"].replace("_pct", "").replace("_", " ")
    tail = {"volume": f"in line with {dn} ({f['driver_pct']:+.1f}%)", "mixed": f"part {dn}, part rate",
            "rate": f"led by {f['top_vendor']}, not {dn}", "unexplained": f"against the {dn} trend, led by {f['top_vendor']}"}
    return f"{ins['title']}, {tail[ins['explanation']]}"


def _prev(p):
    y, m = int(p[:4]), int(p[5:])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _med(xs):
    return int(statistics.median(xs)) if xs else 0


def _fmt(c):
    return f"${abs(c) / 100:,.0f}"


def _fmt2(c):
    return _fmt(c) if abs(c) >= 100000 else f"${abs(c) / 100:,.2f}"   # under $1,000 the number guard needs the cents


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM txns ORDER BY id")]


def _index(rows):
    by_v = {}
    for r in rows:
        by_v.setdefault(r["vendor"], []).append(r)
    return by_v


def _hist(by_v, vendor, period):
    return [r for r in by_v.get(vendor, []) if r["period"] < period]


def _norm(name):
    n = re.sub(r"[^a-z0-9 ]", "", name.lower())
    n = re.sub(r"\bsvcs?\b", "services", n)
    n = re.sub(r"\b(llc|inc|co|corp|ltd|llp)\b", "", n)
    return " ".join(n.split())


def _pct(a, b):
    return round((a - b) / b * 100, 1) + 0.0 if b else 0.0   # + 0.0: never emit -0.0


def _periods(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT period FROM txns ORDER BY period")]


# ---------------------------------------------------------------- flags
def _compute_flags(rows, period):
    by_v = _index(rows)
    cur = sorted((r for r in rows if r["period"] == period), key=lambda r: r["id"])
    out = {r["id"]: [] for r in cur}
    cat_hist = {}
    for r in rows:
        if r["period"] < period and r["kind"] == "activity":
            cat_hist.setdefault(r["category"], []).append(abs(r["amount_cents"]))
    acc_prior = {}
    for r in rows:
        if r["period"] == _prev(period) and r["source"] == "accrual":
            acc_prior.setdefault(r["vendor"], []).append(r)
    revs = {r["vendor"] for r in cur if r["source"] == "reversal"}
    je_preparers = {r["preparer"] for r in rows if r["period"] < period and r["kind"] == "accounting"}
    known = sorted(v for v, rs in by_v.items() if any(x["period"] < period for x in rs))

    def add(t, code, msg):
        out[t["id"]].append({"code": code, "msg": msg})

    groups = {}
    for t in cur:
        if t["kind"] == "activity" and LIMIT * 0.9 <= t["amount_cents"] < LIMIT:
            groups.setdefault((t["vendor"], t["date"]), []).append(t)
    split_ids = []
    for (v, d), g in sorted(groups.items()):
        if len(g) >= 2:
            tot = sum(x["amount_cents"] for x in g)
            for x in g:
                add(x, "split_payment", f"{len(g)} payments to {v} on {d}, each just under the {_fmt(LIMIT)} approval "
                    f"limit (total {_fmt(tot)}).")
                split_ids.append(x["id"])

    for t in cur:
        amt, ab = t["amount_cents"], abs(t["amount_cents"])
        h = _hist(by_v, t["vendor"], period)
        periods_seen = len({x["period"] for x in h})
        med = _med([abs(x["amount_cents"]) for x in h])
        act = t["kind"] == "activity"
        d = date.fromisoformat(t["date"])
        if act and ab >= DUP_MIN and t["id"] not in split_ids:       # duplicate: flag the later copy
            for u in by_v[t["vendor"]]:
                if u["id"] != t["id"] and u["kind"] == "activity" and u["amount_cents"] == amt \
                        and (u["date"], u["id"]) < (t["date"], t["id"]) \
                        and (d - date.fromisoformat(u["date"])).days <= DUP_DAYS:
                    add(t, "duplicate", f"Same vendor and amount as {u['id']} on {u['date']} "
                        f"({(d - date.fromisoformat(u['date'])).days} days earlier). Possible duplicate payment.")
                    break
        if act and not h:
            cm = _med(cat_hist.get(t["category"], []))
            if cm and ab >= NEW_VENDOR_X * cm:
                add(t, "new_vendor", f"New vendor, first payment is {ab / cm:.1f}x the {t['category']} median "
                    f"({_fmt(cm)}).")
            nt = _norm(t["vendor"])
            for v in known:
                if v == t["vendor"]:
                    continue
                r = difflib.SequenceMatcher(None, nt, _norm(v)).ratio()
                vm = _med([abs(x["amount_cents"]) for x in by_v[v] if x["period"] < period])
                if r >= NEAR_DUP_RATIO and vm and abs(ab - vm) <= 0.35 * vm:
                    add(t, "near_dup_vendor", f"Name is {r:.0%} similar to existing vendor \"{v}\" and the amount is "
                        f"similar ({_fmt(ab)} vs typical {_fmt(vm)}). Possible duplicate vendor record.")
                    break
        if not act and d.weekday() >= 5 and t["preparer"] not in je_preparers:
            add(t, "weekend_je", f"Journal entry posted on a {d.strftime('%A')} by "
                f"{USERS.get(t['preparer'], t['preparer'])}, who has never posted a JE before.")
        if act and ab >= 1000000 and ab % 100000 == 0 and not (t["memo"] or "").strip():
            add(t, "round_amount", f"Round amount {_fmt(ab)} with no memo.")
        if act and t["source"] in ("ap", "bank", "card") and t["vendor"] in acc_prior and t["vendor"] not in revs:
            a = acc_prior[t["vendor"]][0]
            add(t, "accrual_no_reversal", f"The {a['period']} accrual of {_fmt(a['amount_cents'])} for {t['vendor']} "
                f"was never reversed, and this invoice is booked too. Possible double count of {_fmt(a['amount_cents'])}.")
        if act and periods_seen >= MIN_PERIODS and len(h) >= 3 and med and ab >= 10000 and ab >= OUTLIER_X * med:
            add(t, "outlier", f"{ab / med:.1f}x this vendor's median ({_fmt(med)}) over {len(h)} prior payments.")
    return out


def _risk(t, flags):
    if not flags:
        return 0
    rs = []
    ab = abs(t["amount_cents"])
    for f in flags:
        c = f["code"]
        if c == "outlier":
            m = float(re.search(r"([\d.]+)x", f["msg"]).group(1))
            rs.append(12 + int(6 * (m - OUTLIER_X)) if m < 2.5 else 30 + int(10 * (m - 2.5)))
        elif c == "new_vendor":
            m = float(re.search(r"([\d.]+)x", f["msg"]).group(1))
            rs.append(min(85, 70 + int(3 * (m - NEW_VENDOR_X))))
        elif c == "round_amount":
            rs.append(62 if ab >= 2000000 else 40)
        else:
            rs.append(BASE_RISK[c])
    return min(99, max(rs) + 5 * (len(rs) - 1))


def flag_period(conn, period):
    rows = _rows(conn)
    flags = _compute_flags(rows, period)
    by_id = {r["id"]: r for r in rows}
    for tid, fl in flags.items():
        conn.execute("UPDATE txns SET flags=?, risk=? WHERE id=?",
                     (json.dumps(fl, sort_keys=True), _risk(by_id[tid], fl), tid))
    conn.commit()


# ---------------------------------------------------------------- stats
def vendor_stats(conn, txn_id):
    t = conn.execute("SELECT * FROM txns WHERE id=?", (txn_id,)).fetchone()
    rows = [dict(r) for r in conn.execute("SELECT period, amount_cents FROM txns WHERE vendor=? AND period<? "
                                          "ORDER BY period, id", (t["vendor"], t["period"]))]
    return {"first_seen": rows[0]["period"] if rows else t["period"], "periods_seen": len({r["period"] for r in rows}),
            "median_cents": _med([abs(r["amount_cents"]) for r in rows]), "history": rows}


def category_stats(conn, txn_id):
    t = conn.execute("SELECT * FROM txns WHERE id=?", (txn_id,)).fetchone()
    ab = [abs(r[0]) for r in conn.execute("SELECT amount_cents FROM txns WHERE category=? AND period<? AND kind='activity'",
                                          (t["category"], t["period"]))]
    tot = lambda p: conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM txns WHERE category=? AND period=?",
                                 (t["category"], p)).fetchone()[0]
    med = _med(ab)
    return {"median_cents": med, "current_total_cents": tot(t["period"]), "prior_total_cents": tot(_prev(t["period"])),
            "multiple_vs_median": round(abs(t["amount_cents"]) / med, 2) if med else None}


# ---------------------------------------------------------------- batch gate
def batch_check(conn, ids=None, user_id=None):
    rows = _rows(conn)
    by_v, by_id = _index(rows), {r["id"]: r for r in rows}
    if ids is None:
        ids = [r["id"] for r in rows if r["period"] == PERIOD and r["status"] == "pending"]
    th = {"approval_limit_cents": LIMIT, "cap_cents": CAP, "min_periods": MIN_PERIODS, "band_pct": int(BAND * 100),
          "jev_fraud_max": JEV_FRAUD, "jev_confidence_min": JEV_CONF, "jev_checked": 0, "jev_skipped": 0, "jev_note": ""}
    eligible, held = [], []
    for tid in sorted(ids):
        t = by_id.get(tid)
        if not t:
            held.append({"id": tid, "reasons": ["Unknown transaction."], "codes": ["not_pending"]})
            continue
        rs, cs = [], []
        if t["status"] != "pending":
            rs.append(f"Status is {t['status']}, not pending."); cs.append("not_pending")
        for f in json.loads(t["flags"] or "[]"):
            rs.append(f["msg"]); cs.append(f["code"])
        if abs(t["amount_cents"]) > CAP:
            rs.append(f"{_fmt(t['amount_cents'])} is over the {_fmt(CAP)} batch cap; needs an individual look."); cs.append("over_cap")
        h = _hist(by_v, t["vendor"], t["period"])
        seen = len({x["period"] for x in h})
        if seen < MIN_PERIODS:
            rs.append(f"No prior history for this vendor; needs {MIN_PERIODS} periods." if not seen else f"Vendor seen in only {seen} prior period(s); needs {MIN_PERIODS}."); cs.append("young_vendor")
        else:
            med = _med([abs(x["amount_cents"]) for x in h])
            if med and abs(abs(t["amount_cents"]) - med) > BAND * med:
                rs.append(f"{_fmt(t['amount_cents'])} is outside +/-{int(BAND * 100)}% of the vendor median {_fmt(med)}.")
                cs.append("off_history")
        if t["jev"]:
            th["jev_checked"] += 1
            j = json.loads(t["jev"])
            # ponytail: JEV holds on probability only. Its confidence is shown to the reviewer but not gated:
            # measured on this seed, half of routine txns score confidence < 0.5, so gating on it would hold 265 of 409.
            if j.get("fraud", 0) >= JEV_FRAUD:
                unsure = " (low confidence: uncertain, you decide)" if j.get("confidence", 1) < JEV_CONF else ""
                rs.append(f"JEV screen: anomaly {j.get('fraud', 0):.0%}, confidence {j.get('confidence', 0):.0%}{unsure}.")
                cs.append("jev")
        else:
            th["jev_skipped"] += 1
        if user_id and t["preparer"] == user_id:
            rs.append("You prepared this transaction (segregation of duties)."); cs.append("sod")
        (held if rs else eligible).append({"id": tid, "reasons": rs, "codes": cs} if rs else tid)
    if th["jev_skipped"]:
        th["jev_note"] = f"{th['jev_skipped']} txn(s) have no JEV score; the JEV gate was skipped for them."
    return {"eligible": eligible, "held": held, "thresholds": th}


# ---------------------------------------------------------------- insights
def _cat_totals(conn):
    tot = {}
    for c, p, s in conn.execute("SELECT category, period, SUM(amount_cents) FROM txns GROUP BY category, period"):
        tot.setdefault(c, {})[p] = s
    return tot


def insights(conn, period):
    tot = _cat_totals(conn)
    pers = _periods(conn)
    prev = _prev(period)
    drv = {r["period"]: dict(r) for r in conn.execute("SELECT * FROM drivers")}
    bud = {(r["period"], r["category"]): r["amount_cents"] for r in conn.execute("SELECT * FROM budget")}
    if prev not in drv or period not in drv:
        return {"insights": [], "kept_out": []}
    older = [p for p in pers if p < period][-3:]
    mat, kept = [], []
    vend = {}
    for c, p, v, s in conn.execute("SELECT category, period, vendor, SUM(amount_cents) FROM txns WHERE period IN (?,?) "
                                   "GROUP BY category, period, vendor", (period, prev)):
        vend.setdefault((c, p), {})[v] = s
    rev = {}
    for r in conn.execute("SELECT * FROM txns WHERE period=? ORDER BY ABS(amount_cents) DESC, id", (period,)):
        rev.setdefault(r["category"], []).append(r)
    for cat in CATEGORIES:
        cur, pri = tot.get(cat, {}).get(period, 0), tot.get(cat, {}).get(prev, 0)
        b = bud.get((period, cat), 0)
        avg3 = int(sum(tot.get(cat, {}).get(p, 0) for p in older) / len(older)) if older else 0
        vp, vb = cur - pri, cur - b
        pp, pb = _pct(cur, pri), _pct(cur, b)
        is_mat = (abs(vp) >= MATERIAL and abs(pp) >= MATERIAL_PCT) or (abs(vb) >= MATERIAL and abs(pb) >= MATERIAL_PCT)
        if not is_mat:
            kept.append({"title": f"{cat}: {_fmt(vp)} {'up' if vp >= 0 else 'down'} ({pp:+.1f}%) vs prior month",
                         "reason": f"Below materiality: needs {_fmt(MATERIAL)} and {MATERIAL_PCT:.0f}% "
                                   f"(vs prior {_fmt(vp)}, {pp:+.1f}%; vs budget {_fmt(vb)}, {pb:+.1f}%).",
                         "amount_cents": abs(vp)})
            continue
        dn = DRIVER[cat]
        dc, dp = drv[period][dn], drv[prev][dn]
        vol = int(round(pri * (dc / dp - 1)))
        rate = vp - vol
        share = vol / vp if vp else 0
        expl = "unexplained" if share < 0 else "volume" if share >= 0.7 else "mixed" if share >= 0.3 else "rate"
        vc, vpv = vend.get((cat, period), {}), vend.get((cat, prev), {})
        tv = max(sorted(set(vc) | set(vpv)), key=lambda v: abs(vc.get(v, 0) - vpv.get(v, 0)))
        td = vc.get(tv, 0) - vpv.get(tv, 0)
        facts = {"current_cents": cur, "prior_cents": pri, "budget_cents": b, "avg3_cents": avg3,
                 "variance_prior_cents": vp, "pct_vs_prior": pp, "variance_budget_cents": vb, "pct_vs_budget": pb,
                 "variance_avg3_cents": cur - avg3, "pct_vs_avg3": _pct(cur, avg3),
                 "driver": dn, "driver_current": dc, "driver_prior": dp, "driver_pct": _pct(dc, dp),
                 "volume_effect_cents": vol, "rate_effect_cents": rate, "volume_share_pct": round(share * 100, 1),
                 "per_driver_current_cents": int(round(cur / dc)), "per_driver_prior_cents": int(round(pri / dp)),
                 "per_driver_pct": _pct(cur / dc, pri / dp), "top_vendor": tv, "top_vendor_delta_cents": td,
                 "top_vendor_share_pct": round(td / vp * 100, 1) if vp else 0.0}
        if dn != "headcount":
            facts.update(headcount_current=drv[period]["headcount"], headcount_prior=drv[prev]["headcount"],
                         headcount_pct=_pct(drv[period]["headcount"], drv[prev]["headcount"]))
        facts.update(occupancy_pct_current=drv[period]["occupancy_pct"], occupancy_pct_prior=drv[prev]["occupancy_pct"],
                     occupancy_change_pct=_pct(drv[period]["occupancy_pct"], drv[prev]["occupancy_pct"]))
        facts.update(_review_facts(rev.get(cat, []), cur, pri, b))
        last7 = pers[-7:]
        if cat in LINE_CATS:
            chart = {"type": "line", "title": f"{cat}, last 7 months", "labels": last7,
                     "series": [{"name": cat, "values": [tot[cat].get(p, 0) for p in last7]},
                                {"name": "Budget", "values": [bud.get((p, cat), 0) for p in last7]}]}
        else:
            chart = {"type": "bar", "title": f"{cat}: prior month, budget, current", "labels": ["Prior month", "Budget", "Current"],
                     "series": [{"name": cat, "values": [pri, b, cur]}]}
        mat.append({"id": SLUG.get(cat, "ins-" + re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-")),
                    "title": f"{cat} {'up' if vp >= 0 else 'down'} {abs(pp):.1f}% vs prior month", "category": cat,
                    "facts": facts, "explanation": expl, "chart": chart})
    mat.sort(key=lambda i: (ORDER.index(i["category"]) if i["category"] in ORDER else 99, -abs(i["facts"]["variance_prior_cents"])))
    small = []
    for r in conn.execute("SELECT * FROM txns WHERE period=? AND flags LIKE '%\"outlier\"%' AND kind='activity'", (period,)):
        for f in json.loads(r["flags"]):
            if f["code"] == "outlier" and abs(r["amount_cents"]) < MATERIAL:
                small.append((float(re.search(r"([\d.]+)x", f["msg"]).group(1)), r["id"], r, f["msg"]))
    for m, tid, r, msg in sorted(small, key=lambda x: (-x[0], x[1]))[:TOP_N_COFFEE]:
        kept.insert(0, {"title": f"{r['vendor']} {_fmt(r['amount_cents'])} on {r['date']} ({tid})",
                        "reason": f"Flagged at transaction level ({m:.1f}x vendor median) but {_fmt(r['amount_cents'])} is far "
                                  f"below the {_fmt(MATERIAL)} materiality line, so it is not a story.",
                        "amount_cents": abs(r["amount_cents"]), "txn_id": tid})
    return {"insights": mat, "kept_out": kept}


def _review_facts(rows, cur, pri, b):
    """Review decisions for one category. Booked figures stay as they are; the adjusted ones sit next to them."""
    items, adj, open_n, open_c = [], 0, 0, 0
    for r in rows:                                   # rows arrive largest first
        e, flagged = effect_cents(r), (r["flags"] or "[]") != "[]"
        adj += e
        if flagged and r["status"] not in DECIDED:
            open_n, open_c = open_n + 1, open_c + r["amount_cents"]
        if (flagged or e) and len(items) < 5:
            res = resolution(r) or {}
            items.append({"txn_id": r["id"], "vendor": r["vendor"], "amount_cents": r["amount_cents"], "state": state(r),
                          "resolution": res.get("code"), "months": res.get("months"), "recover_cents": res.get("recover_cents"),
                          "effect_cents": e, "note": r["approve_note"] or "", "by_name": USERS.get(r["approver"])})
    out = {"review_items": items, "review_open_count": open_n, "review_open_cents": open_c, "adjustment_cents": adj}
    if adj:
        a = cur + adj
        out.update(adjusted_cents=a, adjusted_variance_prior_cents=a - pri, adjusted_pct_vs_prior=_pct(a, pri),
                   adjusted_variance_budget_cents=a - b, adjusted_pct_vs_budget=_pct(a, b))
    return out


def _review_text(f, cat):
    out = ""
    if f.get("adjustment_cents"):
        v = lambda i: i["vendor"] if not re.search(r"\d", i["vendor"]) else "one vendor"   # a digit in a name would read as a figure
        said = []
        for i in f.get("review_items", []):
            if i["state"] == "cancelled":
                said.append(f"{v(i)} {'-' if i['amount_cents'] < 0 else ''}{_fmt2(i['amount_cents'])} cancelled")
            elif i["resolution"] == "refund_request" and i["effect_cents"]:
                said.append(f"{_fmt2(i['recover_cents'])} refund requested from {v(i)}")
            elif i["resolution"] == "prepayment" and i["effect_cents"]:
                said.append(f"{v(i)} {_fmt2(i['amount_cents'])} prepayment spread over {i['months']} months")
        if sum(i["effect_cents"] for i in f.get("review_items", [])) != f["adjustment_cents"]:
            said.append("other items")               # review_items is capped, the total is not
        out += (f" Review adjustments of {'+' if f['adjustment_cents'] > 0 else '-'}{_fmt2(f['adjustment_cents'])}"
                f"{' (' + '; '.join(said) + ')' if said else ''} bring "
                f"{cat} to {_fmt(f['adjusted_cents'])}, {_dir(f['adjusted_variance_prior_cents'])} "
                f"{abs(f['adjusted_pct_vs_prior']):.1f}% vs the prior month.")
    if f.get("review_open_count"):
        n = f["review_open_count"]
        out += f" {n} flagged {'item is' if n == 1 else 'items are'} still open ({_fmt2(f['review_open_cents'])})."
    return out


def template_narrative(ins):
    f, cat = ins["facts"], ins["category"]
    dn = f["driver"].replace("_pct", "").replace("_", " ")
    unit = {"headcount": "head", "customers": "customer", "occupancy": "occupancy point"}[dn]
    up = "up" if f["variance_prior_cents"] >= 0 else "down"
    what = (f"{cat} was {_fmt(f['current_cents'])} this month, {up} {_fmt(f['variance_prior_cents'])} "
            f"({abs(f['pct_vs_prior']):.1f}%) vs the prior month and {_fmt(f['variance_budget_cents'])} "
            f"{'over' if f['variance_budget_cents'] >= 0 else 'under'} budget ({abs(f['pct_vs_budget']):.1f}%).")
    ctx = (f"Prior month {_fmt(f['prior_cents'])}, budget {_fmt(f['budget_cents'])}, trailing average {_fmt(f['avg3_cents'])}. "
           f"{dn.capitalize()} moved from {f['driver_prior']:g} to {f['driver_current']:g} ({f['driver_pct']:+.1f}%).")
    ex = ins["explanation"]
    if ex == "volume":
        why = (f"Volume explains {_fmt(f['volume_effect_cents'])} of the change; spend per {unit} moved only "
               f"{f['per_driver_pct']:+.1f}% to {_fmt(f['per_driver_current_cents'])}, so this is volume-driven, not price.")
    elif ex == "mixed":
        why = (f"Volume explains {_fmt(f['volume_effect_cents'])} and rate (spend per {unit}) explains "
               f"{_fmt(f['rate_effect_cents'])}; both moved.")
    elif ex == "rate":
        why = (f"The {dn} change explains only {_fmt(f['volume_effect_cents'])}; the other {_fmt(f['rate_effect_cents'])} is not "
               f"volume. Largest mover: {f['top_vendor']} ({_fmt(f['top_vendor_delta_cents'])}, {abs(f['top_vendor_share_pct']):.1f}% "
               f"of the change). Needs a human explanation.")
    else:
        why = (f"The {dn} change would imply {_fmt(f['volume_effect_cents'])}, yet spend moved the other way, so volume "
               f"does not explain it. Largest mover: {f['top_vendor']} ({_fmt(f['top_vendor_delta_cents'])}). "
               f"Check timing of invoices or a planned item.")
    return {"what": what, "context": ctx, "why": why + _review_text(f, cat)}


# ---------------------------------------------------------------- number guard
NUM = re.compile(r"(?<![A-Za-z_.])[-\u2212+]?\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|[kKmM]\b|x\b))?")
IDLIKE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{4}-\d{4}\b|\b[A-Za-z][A-Za-z0-9]*-\d+\b")   # periods, dates and txn ids are labels, not figures


def _parse(tok):
    s = tok.strip().lstrip("-\u2212+").lstrip("$").replace(",", "").replace(" ", "")   # sign dropped: "fell -99%" is checked as 99
    mult = 1.0
    if s[-1:] in "kK": mult, s = 1e3, s[:-1]
    elif s[-1:] in "mM": mult, s = 1e6, s[:-1]
    elif s[-1:] in "%x": s = s[:-1]
    try:
        v = float(s) * mult
    except ValueError:
        return None
    return v


def _is_year(tok, v):
    return tok.isdigit() and 2020 <= v <= 2030


def _close(a, b):
    # ponytail: small numbers (percents, counts) must match to 0.05; large amounts to 0.5% so "$184,351" matches 184,350.51
    return abs(a - b) <= (0.05 if max(abs(a), abs(b)) < 1000 else 0.005 * max(abs(a), abs(b)))


def number_guard(text, allowed_numbers, user_text=""):
    allowed = [abs(float(a)) for a in allowed_numbers]
    mask = lambda t: IDLIKE.sub(lambda m: " " * len(m.group()), t)
    for m in NUM.finditer(mask(user_text or "")):
        v = _parse(m.group())
        if v is not None:
            allowed.append(abs(v))
    nums, bad, out, pos = [], [], [], 0
    for m in NUM.finditer(mask(text)):
        tok = text[m.start():m.end()]
        v = _parse(tok)
        if v is None or _is_year(tok, v):
            continue
        nums.append(tok)
        out.append(html.escape(text[pos:m.start()]))
        if any(_close(abs(v), a) for a in allowed):
            out.append(html.escape(tok))
        else:
            bad.append(tok)
            out.append(f'<mark class="unverified">{html.escape(tok)}</mark>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return {"numbers": nums, "unverified": bad, "marked_html": "".join(out)}


def facts_numbers(facts):
    out = set()

    def walk(k, v):
        if isinstance(v, dict):
            for kk, vv in v.items():
                walk(kk, vv)
        elif isinstance(v, (list, tuple)):
            for vv in v:
                walk(k, vv)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            a = abs(float(v))
            out.add(a / 100 if k.endswith("_cents") else a)   # raw cents are never a figure a person would write
    for k, v in facts.items():
        walk(k, v)
    return out


# ---------------------------------------------------------------- JEV state
def jev_state(conn, txn_id):
    t = conn.execute("SELECT * FROM txns WHERE id=?", (txn_id,)).fetchone()
    v, c = vendor_stats(conn, txn_id), category_stats(conn, txn_id)
    return {"txn": {k: t[k] for k in ("vendor", "category", "amount_cents", "date", "kind", "source", "memo", "preparer")},
            "vendor_history": {"periods_seen": v["periods_seen"], "median_cents": v["median_cents"], "first_seen": v["first_seen"]},
            "category": {"median_cents": c["median_cents"], "multiple_vs_median": c["multiple_vs_median"]},
            "code_flags": [f["code"] for f in json.loads(t["flags"] or "[]")]}


# ---------------------------------------------------------------- canonical package
def _ids(s):
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else [v]
    except (TypeError, ValueError):
        return [x.strip() for x in str(s or "").split(",") if x.strip()]


def build_package(conn, period):
    txns = []
    for r in conn.execute("SELECT * FROM txns WHERE period=? ORDER BY id", (period,)):
        j = json.loads(r["jev"]) if r["jev"] else None
        txns.append({**{k: r[k] for k in ("id", "date", "vendor", "category", "account", "amount_cents", "kind", "source",
                                          "status", "preparer", "approver", "approve_note", "approve_ts", "memo", "source_ref")},
                     "resolution": resolution(r),
                     "flags": [f["code"] for f in json.loads(r["flags"] or "[]")],
                     "jev": {k: j.get(k) for k in ("fraud", "confidence", "risk_type")} if j else None})
    inp = {t["id"] for t in txns}
    routes = [{"id": r["id"], "txn_ids": _ids(r["txn_ids"]), "from_user": r["from_user"], "to_user": r["to_user"],
               "reason": r["reason"], "note": r["note"], "status": r["status"], "reply": r["reply"]}
              for r in conn.execute("SELECT * FROM routes ORDER BY id") if inp & set(_ids(r["txn_ids"]))]
    com = {r["insight_id"]: dict(r) for r in conn.execute("SELECT * FROM commentary WHERE pinned=1")}
    ins = [{"id": i["id"], "title": i["title"], "facts": i["facts"], "commentary_final": com[i["id"]]["final"], "pinned": True}
           for i in insights(conn, period)["insights"] if i["id"] in com]
    obj = {"v": 1, "company": COMPANY, "period": period, "source_system": "QuickBooks Online (synthetic export)",
           "txns": txns, "routes": routes, "insights": ins,
           "drivers": [dict(r) for r in conn.execute("SELECT * FROM drivers ORDER BY period")],
           "budget": [dict(r) for r in conn.execute("SELECT * FROM budget ORDER BY period, category")]}
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- deck
def _label(p):
    return f"{calendar.month_name[int(p[5:])]} {p[:4]}"


def _line(label, typ, actual, adj, prior, budget, **extra):
    """One P&L line. Variances are on booked amounts; favourable is by business impact (revenue up good, expense up bad)."""
    vp, vb = actual - prior, actual - budget
    good = (lambda v: v >= 0) if typ == "revenue" else (lambda v: v <= 0)
    pc = lambda v, base: round(v / abs(base) * 100, 1) + 0.0 if base else 0.0   # abs: a loss-making result keeps the sign of its variance
    return {"label": label, "type": typ, "actual_cents": actual, "adjustment_cents": adj, "adjusted_cents": actual + adj,
            "prior_cents": prior, "budget_cents": budget, "var_prior_cents": vp, "var_prior_pct": pc(vp, prior),
            "fav_prior": good(vp), "var_budget_cents": vb, "var_budget_pct": pc(vb, budget), "fav_budget": good(vb), **extra}


def deck(conn, period):
    """Everything the close deck shows that does not depend on who is looking. Every total is the sum of the rows emitted."""
    tot, pers, prev = _cat_totals(conn), _periods(conn)[-13:], _prev(period)
    bud = {(r["period"], r["category"]): r["amount_cents"] for r in conn.execute("SELECT * FROM budget")}
    drv = {r["period"]: dict(r) for r in conn.execute("SELECT * FROM drivers")}
    txns = conn.execute("SELECT * FROM txns WHERE period=? ORDER BY ABS(amount_cents) DESC, id", (period,)).fetchall()
    data = insights(conn, period)
    mat = {i["category"] for i in data["insights"]}
    adj, adjs = {}, []
    for r in txns:
        e = effect_cents(r)
        if e:
            typ = "cancelled" if r["status"] == "cancelled" else resolution(r)["code"]
            adj.setdefault((r["category"], typ), 0)
            adj[(r["category"], typ)] += e
            adjs.append({"txn_id": r["id"], "vendor": r["vendor"], "category": r["category"], "type": typ,
                         "amount_cents": r["amount_cents"], "effect_cents": e, "note": r["approve_note"] or "",
                         "by_name": USERS.get(r["approver"])})
    cadj = lambda c: sum(v for (cc, _), v in adj.items() if cc == c)
    g = lambda c, p: tot.get(c, {}).get(p, 0)
    rows = [_line(c, t, g(c, period), cadj(c), g(c, prev), bud.get((period, c), 0), material=c in mat) for c, t in CATEGORIES.items()]
    sm = lambda typ, k: sum(r[k] for r in rows if r["type"] == typ)
    keys = ("actual_cents", "adjustment_cents", "prior_cents", "budget_cents")
    rv, ox = [sm("revenue", k) for k in keys], [sm("expense", k) for k in keys]
    totals = {"revenue": _line("Total revenue", "revenue", *rv), "opex": _line("Total operating expenses", "expense", *ox),
              "result": _line("Operating result", "revenue", *[a - b for a, b in zip(rv, ox)])}
    kpis = [{"key": k, "label": lab, "value_cents": t["actual_cents"],
             **{x: t[x] for x in ("adjusted_cents", "prior_cents", "budget_cents", "var_prior_cents", "var_prior_pct", "fav_prior",
                                  "var_budget_cents", "var_budget_pct", "fav_budget")}}
            for k, lab, t in (("revenue", "Revenue", totals["revenue"]), ("opex", "Operating expenses", totals["opex"]),
                              ("result", "Operating result", totals["result"]))]
    mg = lambda k: round(totals["result"][k] / totals["revenue"][k] * 100, 1) if totals["revenue"][k] else 0.0
    m = [mg("actual_cents"), mg("prior_cents"), mg("budget_cents")]
    kpis.append({"key": "margin", "label": "Operating margin", "value_pct": m[0], "prior_pct": m[1], "budget_pct": m[2],
                 "var_prior_pts": round(m[0] - m[1], 1), "fav_prior": m[0] >= m[1]})
    for k, lab in (("headcount", "Headcount"), ("customers", "Customers")):
        if period in drv and prev in drv:
            kpis.append({"key": k, "label": lab, "value": drv[period][k], "prior": drv[prev][k], "var_prior_pct": _pct(drv[period][k], drv[prev][k])})
    ps = lambda typ, p: sum(g(c, p) for c, t in CATEGORIES.items() if t == typ)
    bs = lambda typ, p: sum(bud.get((p, c), 0) for c, t in CATEGORIES.items() if t == typ)
    trend = {"labels": pers, "revenue_cents": [ps("revenue", p) for p in pers], "opex_cents": [ps("expense", p) for p in pers],
             "result_cents": [ps("revenue", p) - ps("expense", p) for p in pers],
             "budget_revenue_cents": [bs("revenue", p) for p in pers], "budget_opex_cents": [bs("expense", p) for p in pers]}
    # opex bridge: prior month -> booked (6 largest category moves + Other) -> after review adjustments
    ex = sorted((r for r in rows if r["type"] == "expense"), key=lambda r: (-abs(r["var_prior_cents"]), r["label"]))
    steps = [{"label": r["label"], "delta_cents": r["var_prior_cents"], "favorable": r["var_prior_cents"] <= 0} for r in ex[:6]]
    other = sum(r["var_prior_cents"] for r in ex[6:])
    steps.append({"label": "Other", "delta_cents": other, "favorable": other <= 0})
    words = {"cancelled": "entry cancelled", "refund_request": "refund requested", "prepayment": "prepayment spread"}
    badj = [{"label": f"{c}: {words[t]}", "delta_cents": v, "favorable": v <= 0}
            for (c, t), v in sorted(adj.items()) if CATEGORIES[c] == "expense"]
    o = totals["opex"]
    bridge = {"start": {"label": f"{_label(prev)} operating expenses", "value_cents": o["prior_cents"]}, "steps": steps,
              "booked": {"label": f"{_label(period)} as booked", "value_cents": o["actual_cents"]}, "adjustments": badj,
              "end": {"label": f"{_label(period)} after review", "value_cents": o["adjusted_cents"]}}
    n = lambda f: sum(1 for r in txns if f(r))
    flagged = lambda r: (r["flags"] or "[]") != "[]"
    # ponytail: batch / shortcut are told apart by the note the server writes; add an approve_via column if notes ever become free text there
    batch = n(lambda r: r["status"] == "approved" and r["approve_note"] == "batch gate passed")
    short = n(lambda r: r["status"] == "approved" and (r["approve_note"] or "").startswith("Demo shortcut"))
    appr = n(lambda r: r["status"] == "approved")
    controls = {"total": len(txns), "approved": appr, "cancelled": n(lambda r: r["status"] == "cancelled"),
                "pending": n(lambda r: r["status"] == "pending"), "routed": n(lambda r: r["status"] == "routed"),
                "batch_approved": batch, "individual": appr - batch - short, "shortcut": short, "flagged": n(flagged),
                "flagged_open": n(lambda r: flagged(r) and r["status"] not in DECIDED), "jev_screened": n(lambda r: bool(r["jev"])),
                "jev_held": n(lambda r: bool(r["jev"]) and json.loads(r["jev"]).get("fraud", 0) >= JEV_FRAUD),
                "adjustment_total_cents": sum(a["effect_cents"] for a in adjs), "adjustments": adjs}
    by_id = {r["id"]: r for r in txns}
    open_items = []
    for rt in conn.execute("SELECT * FROM routes WHERE status='open' ORDER BY id"):
        for tid in _ids(rt["txn_ids"]):
            t = by_id.get(tid)
            if t:
                refund = rt["reason"] == "refund_request"
                open_items.append({"txn_id": tid, "vendor": t["vendor"], "amount_cents": t["amount_cents"],
                                   "type": "refund_request" if refund else "route",
                                   "recover_cents": (resolution(t) or {}).get("recover_cents") if refund else None,
                                   "owner_name": USERS.get(rt["to_user"], rt["to_user"]), "status": rt["status"],
                                   "note": rt["note"] or "", "reply": rt["reply"]})
    for i in data["insights"]:
        c = i["category"]
        i.update(type=CATEGORIES[c], headline=headline(i),
                 series13={"labels": pers, "actual_cents": [g(c, p) for p in pers], "budget_cents": [bud.get((p, c), 0) for p in pers]})
    return {"company": COMPANY, "period": period, "period_label": _label(period), "prior_label": _label(prev), "units": "USD",
            "synthetic": True, "kpis": kpis, "pnl": {"rows": rows, "totals": totals}, "trend": trend, "bridge": bridge,
            "insights": data["insights"], "controls": controls, "open_items": open_items, "kept_out": data["kept_out"]}


def package_hash(b):
    return hashlib.sha256(b).hexdigest()


def diff_packages(old, new):
    load = lambda x: x if isinstance(x, dict) else json.loads(x)
    o = {t["id"]: t for t in load(old)["txns"]}
    n = {t["id"]: t for t in load(new)["txns"]}
    out = []
    for i in sorted(set(o) | set(n)):
        if i not in n:
            out.append({"id": i, "field": "__row__", "was": "present", "now": None})
        elif i not in o:
            out.append({"id": i, "field": "__row__", "was": None, "now": "present"})
        else:
            for k in sorted(set(o[i]) | set(n[i])):
                if o[i].get(k) != n[i].get(k):
                    out.append({"id": i, "field": k, "was": o[i].get(k), "now": n[i].get(k)})
    lo, ln = load(old), load(new)
    for sec in sorted((set(lo) | set(ln)) - {"txns"}):          # routes, insights, budget...: name the section even when rows are not itemised
        if lo.get(sec) != ln.get(sec):
            out.append({"id": sec, "field": "__section__", "was": "changed", "now": "changed"})
    return out
