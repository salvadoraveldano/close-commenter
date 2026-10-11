#!/usr/bin/env python3
"""The one check: no network and no secrets, < 20 s, prints OK. One function per section so API-level checks can be appended."""
import json, os, shutil, sqlite3, subprocess, sys, tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine, seed

P = engine.PERIOD
planted_ids_by_code = {}
EXPECTED_INSIGHTS = ["ins-cloud-hosting", "ins-marketing", "ins-office-admin", "ins-payroll", "ins-revenue"]


def connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def planted(c):
    return json.loads(c.execute("SELECT value FROM meta WHERE key='planted'").fetchone()[0])


def meta(c, k):
    return c.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()[0]


def one(c, sql, *a):
    return c.execute(sql, a).fetchone()


def check_deterministic(tmp):
    """two separate processes with different string-hash seeds must give the same package hash"""
    hashes = []
    for hs in ("1", "2"):
        path = os.path.join(tmp, f"det{hs}.db")
        code = ("import sys,sqlite3; sys.path.insert(0,sys.argv[1]); import seed,engine; seed.build(sys.argv[2]); "
                "c=sqlite3.connect(sys.argv[2]); c.row_factory=sqlite3.Row; print(engine.package_hash(engine.build_package(c,'2026-09')))")
        out = subprocess.run([sys.executable, "-c", code, HERE, path], capture_output=True, text=True,
                             env={**os.environ, "PYTHONHASHSEED": hs}, check=True).stdout.strip()
        hashes.append(out)
    assert hashes[0] == hashes[1] and len(hashes[0]) == 64, f"seed not deterministic: {hashes}"


def check_counts(c):
    per = dict(c.execute("SELECT period, COUNT(*) FROM txns GROUP BY period").fetchall())
    assert sorted(per) == seed.PERIODS and len(per) == 13, per
    for p in seed.PERIODS[:-1]:
        assert 330 <= per[p] <= 380, (p, per[p])
    assert 400 <= per[P] <= 420, per[P]
    assert one(c, "SELECT COUNT(*) FROM txns WHERE period<? AND (status!='approved' OR approve_ts IS NULL OR approver NOT IN ('u_controller','u_cfo'))", P)[0] == 0
    assert one(c, "SELECT COUNT(*) FROM txns WHERE period=? AND status!='pending'", P)[0] == 0
    assert one(c, "SELECT COUNT(*) FROM txns WHERE preparer=approver")[0] == 0
    assert one(c, "SELECT COUNT(*) FROM txns WHERE source_ref NOT LIKE 'qbo:%/%'")[0] == 0
    assert one(c, "SELECT COUNT(DISTINCT vendor) FROM txns WHERE kind='activity' AND category NOT LIKE '%Revenue'")[0] >= 55
    assert one(c, "SELECT COUNT(DISTINCT vendor) FROM txns WHERE category LIKE '%Revenue'")[0] >= 25
    d = {r["period"]: r for r in c.execute("SELECT * FROM drivers")}
    assert (d["2025-09"]["headcount"], d["2026-08"]["headcount"], d[P]["headcount"]) == (40, 46, 52)
    assert (d["2026-08"]["occupancy_pct"], d[P]["occupancy_pct"]) == (58, 71)
    assert d[P]["customers"] > d["2026-08"]["customers"]
    assert one(c, "SELECT COUNT(*) FROM budget")[0] == 13 * len(engine.CATEGORIES)
    assert one(c, "SELECT COUNT(*) FROM users")[0] == 5
    # accounting-only entries every month
    for p in seed.PERIODS:
        kinds = {r[0] for r in c.execute("SELECT DISTINCT source FROM txns WHERE period=? AND kind='accounting'", (p,))}
        assert {"accrual", "depr", "reclass", "je"} <= kinds and (p == seed.PERIODS[0] or "reversal" in kinds or p == P), (p, kinds)
    return per[P]


def check_planted(c):
    pl = planted(c)
    assert sorted(pl) == sorted(engine.CODES), sorted(pl)
    for code, ids in pl.items():
        assert ids, code
        for i in ids:
            fl = [f["code"] for f in json.loads(one(c, "SELECT flags FROM txns WHERE id=?", i)["flags"])]
            assert code in fl, (code, i, fl)
    assert pl["duplicate"] == ["2609-0217"]
    dup = one(c, "SELECT * FROM txns WHERE id='2609-0217'")
    first = [r for r in c.execute("SELECT * FROM txns WHERE vendor=? AND amount_cents=? AND id!=?", (dup["vendor"], dup["amount_cents"], dup["id"]))
             if r["period"] == P]
    assert len(first) == 1 and (date.fromisoformat(dup["date"]) - date.fromisoformat(first[0]["date"])).days == 3
    sp = [one(c, "SELECT * FROM txns WHERE id=?", i) for i in pl["split_payment"]]
    assert len(sp) == 3 and {(t["vendor"], t["date"], t["amount_cents"]) for t in sp} == {(sp[0]["vendor"], sp[0]["date"], 490000)}
    assert all("duplicate" not in [f["code"] for f in json.loads(t["flags"])] for t in sp)
    nv = one(c, "SELECT * FROM txns WHERE id=?", pl["new_vendor"][0])
    assert nv["vendor"] == "Nimbus Compute LLC" and engine.vendor_stats(c, nv["id"])["periods_seen"] == 0
    assert 4.5 <= engine.category_stats(c, nv["id"])["multiple_vs_median"] <= 5.5
    wj = one(c, "SELECT * FROM txns WHERE id=?", pl["weekend_je"][0])
    assert date.fromisoformat(wj["date"]).weekday() == 6 and wj["kind"] == "accounting"
    assert one(c, "SELECT COUNT(*) FROM txns WHERE kind='accounting' AND preparer=? AND period<?", wj["preparer"], P)[0] == 0
    ra = one(c, "SELECT * FROM txns WHERE id=?", pl["round_amount"][0])
    assert ra["amount_cents"] == 2500000 and not ra["memo"]
    assert one(c, "SELECT COUNT(*) FROM txns WHERE vendor IN ('Acme Cloud Svcs','Acme Cloud Services')")[0] > 1
    assert one(c, "SELECT vendor FROM txns WHERE id=?", pl["near_dup_vendor"][0])[0] == "Acme Cloud Svcs"
    av = one(c, "SELECT vendor FROM txns WHERE id=?", pl["accrual_no_reversal"][0])[0]
    assert one(c, "SELECT COUNT(*) FROM txns WHERE vendor=? AND source='accrual' AND period='2026-08'", av)[0] == 1
    assert one(c, "SELECT COUNT(*) FROM txns WHERE vendor=? AND source='reversal' AND period=?", av, P)[0] == 0
    ou = one(c, "SELECT * FROM txns WHERE id=?", pl["outlier"][0])
    assert ou["category"] == "Professional Fees" and 5.5 <= ou["amount_cents"] / engine.vendor_stats(c, ou["id"])["median_cents"] <= 6.5
    # tamper target
    t = one(c, "SELECT * FROM txns WHERE id='2609-0231'")
    assert t["kind"] == "activity" and t["amount_cents"] == 498000 and t["flags"] == "[]" and t["risk"] == 0
    assert engine.vendor_stats(c, t["id"])["periods_seen"] >= 3
    # coffee: flagged at txn level
    cof = one(c, "SELECT * FROM txns WHERE id=?", meta(c, "coffee_id"))
    assert cof["amount_cents"] == 195000 and "outlier" in cof["flags"]
    n = one(c, "SELECT COUNT(*) FROM txns WHERE period=? AND flags!='[]'", P)[0]
    assert 25 <= n <= 40, n
    assert one(c, "SELECT COUNT(*) FROM txns WHERE period<? AND flags!='[]'", P)[0] == 0
    return n


def check_batch(c):
    pl = planted(c)
    planted_ids = sorted({i for v in pl.values() for i in v})
    b = engine.batch_check(c)
    assert len(b["eligible"]) >= 35, len(b["eligible"])
    assert not set(b["eligible"]) & set(planted_ids), set(b["eligible"]) & set(planted_ids)
    assert "2609-0231" in b["eligible"]
    held = {h["id"]: h for h in b["held"]}
    for i in planted_ids:
        assert i in held and held[i]["reasons"], i
    for code, ids in pl.items():
        for i in ids:
            assert code in held[i]["codes"], (code, i)
    assert any("over_cap" in h["codes"] for h in b["held"]) and any("young_vendor" in h["codes"] for h in b["held"])
    assert len(b["eligible"]) + len(b["held"]) == one(c, "SELECT COUNT(*) FROM txns WHERE period=?", P)[0]
    nj = sqlite3.connect(":memory:"); nj.row_factory = sqlite3.Row
    c.backup(nj)
    nj.execute("UPDATE txns SET jev=NULL")                  # no cached screen: the gate says so instead of guessing
    bn = engine.batch_check(nj)
    assert bn["thresholds"]["jev_skipped"] > 0 and "no JEV" in bn["thresholds"]["jev_note"]
    for u in ("u_controller", "u_cfo"):
        bu = engine.batch_check(c, user_id=u)
        assert len(bu["eligible"]) >= 35 and not any(i in bu["eligible"] for i in planted_ids), u
    assert any("sod" in h["codes"] for h in engine.batch_check(c, user_id="u_preparer")["held"])
    # explicit ids of a planted txn are still refused
    assert engine.batch_check(c, ids=["2609-0217"])["eligible"] == []
    # JEV gate: a bad screen holds an otherwise-clean txn; a good one passes
    cc = sqlite3.connect(":memory:"); cc.row_factory = sqlite3.Row
    c.backup(cc)
    cc.execute("UPDATE txns SET jev=? WHERE id='2609-0231'", (json.dumps({"fraud": .9, "confidence": .9, "risk_type": "normal"}),))
    assert "jev" in engine.batch_check(cc, ids=["2609-0231"])["held"][0]["codes"]
    cc.execute("UPDATE txns SET jev=? WHERE id='2609-0231'", (json.dumps({"fraud": .6, "confidence": .3, "risk_type": "normal"}),))
    h = engine.batch_check(cc, ids=["2609-0231"])["held"][0]
    assert "jev" in h["codes"] and "uncertain" in h["reasons"][0]   # high probability, low confidence: held, human decides
    cc.execute("UPDATE txns SET jev=? WHERE id='2609-0231'", (json.dumps({"fraud": .1, "confidence": .3, "risk_type": "normal"}),))
    assert engine.batch_check(cc, ids=["2609-0231"])["eligible"] == ["2609-0231"]   # low confidence alone does not hold
    cc.execute("UPDATE txns SET jev=? WHERE id='2609-0231'", (json.dumps({"fraud": .1, "confidence": .9, "risk_type": "normal"}),))
    assert engine.batch_check(cc, ids=["2609-0231"])["eligible"] == ["2609-0231"]
    return len(b["eligible"]), len(b["held"])


def check_insights(c):
    r = engine.insights(c, P)
    ids = sorted(i["id"] for i in r["insights"])
    assert ids == EXPECTED_INSIGHTS, ids
    by = {i["id"]: i for i in r["insights"]}
    for i in r["insights"]:
        assert i["facts"] and i["chart"]["type"] in ("bar", "line") and i["chart"]["series"] and i["title"]
        assert i["explanation"] in ("volume", "rate", "mixed", "unexplained")
        n = engine.template_narrative(i)
        assert n["what"] and n["context"] and n["why"]
        g = engine.number_guard(" ".join(n.values()), engine.facts_numbers(i["facts"]))
        assert g["unverified"] == [], (i["id"], g["unverified"])      # template text is built only from facts
        assert abs(i["facts"]["variance_prior_cents"]) >= engine.MATERIAL or abs(i["facts"]["variance_budget_cents"]) >= engine.MATERIAL
    o = by["ins-office-admin"]["facts"]
    assert by["ins-office-admin"]["explanation"] == "volume" and o["variance_prior_cents"] > 0 and o["variance_budget_cents"] > 0
    assert abs(o["per_driver_pct"]) < 3 and o["driver"] == "headcount" and (o["driver_prior"], o["driver_current"]) == (46, 52)
    cl = by["ins-cloud-hosting"]
    assert cl["explanation"] in ("unexplained", "rate") and cl["facts"]["top_vendor"] == "Nimbus Compute LLC"
    assert by["ins-revenue"]["explanation"] == "volume" and by["ins-revenue"]["facts"]["variance_prior_cents"] > 0
    mk = by["ins-marketing"]["facts"]
    assert mk["variance_budget_cents"] < 0 and mk["variance_prior_cents"] < 0
    assert by["ins-payroll"]["explanation"] == "volume" and by["ins-payroll"]["facts"]["driver"] == "headcount"
    ko = r["kept_out"]
    cid = meta(c, "coffee_id")
    assert any(k.get("txn_id") == cid and k["amount_cents"] == 195000 for k in ko), "coffee txn not in kept_out"
    assert all({"title", "reason", "amount_cents"} <= set(k) for k in ko)
    assert sum(1 for k in ko if "Below materiality" in k["reason"]) == len(engine.CATEGORIES) - len(r["insights"])
    return len(r["insights"]), len(ko)


def check_number_guard(c):
    ins = {i["id"]: i for i in engine.insights(c, P)["insights"]}["ins-office-admin"]
    allowed = engine.facts_numbers(ins["facts"])
    rec = json.load(open(os.path.join(HERE, "cache", "enhance_recorded.json")))["ins-office-admin"]
    assert len(rec["draft_hint"].split()) >= 25 and "recorded example" in rec["label"].lower()
    g = engine.number_guard(rec["enhanced"], allowed)
    assert g["unverified"] == ["35%"], g["unverified"]              # exactly the invented figure
    assert '<mark class="unverified">35%</mark>' in g["marked_html"] and g["marked_html"].count("<mark") == 1
    assert len(g["numbers"]) >= 8
    f = ins["facts"]
    ok = engine.number_guard("Spend rose 13.6% to $184,351, headcount is up 13% to 52, occupancy 71%, per head $3,545, $22k up, 5.1x.",
                             allowed | {5.1})
    assert ok["unverified"] == [], ok["unverified"]
    bad = engine.number_guard("Headcount grew 35%, costs rose $99,999 & <b>x</b>", allowed)
    assert bad["unverified"] == ["35%", "$99,999"] and "&lt;b&gt;" in bad["marked_html"]
    assert engine.number_guard("grew 35%", allowed, user_text="I think we grew 35% here")["unverified"] == []
    assert engine.number_guard("In 2026 we closed Q3 and ins-1", allowed)["unverified"] == []
    assert 13.0 in allowed and f["driver_pct"] == 13.0 and not (f["current_cents"] in allowed)     # percents as percents, cents only as dollars
    bad = engine.number_guard("Office spend was $18,435,051 and fell -99%, grew 13.6-99%, closed 2609-0217 in 2026-09", allowed)
    assert bad["unverified"] == ["$18,435,051", "-99%", "-99%"], bad["unverified"]               # cents shown as dollars, sign and range upper bound are checked; ids and periods are labels


def check_package(c, tmp):
    b1, b2 = engine.build_package(c, P), engine.build_package(c, P)
    assert b1 == b2 and engine.package_hash(b1) == engine.package_hash(b2)
    obj = json.loads(b1)
    assert obj["v"] == 1 and obj["period"] == P and len(obj["txns"]) == one(c, "SELECT COUNT(*) FROM txns WHERE period=?", P)[0]
    assert [t["id"] for t in obj["txns"]] == sorted(t["id"] for t in obj["txns"]) and obj["insights"] == [] and obj["routes"] == []
    assert b1 == json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    tam = os.path.join(tmp, "tamper.db")
    shutil.copy(c.execute("PRAGMA database_list").fetchone()[2], tam)
    t = connect(tam)
    t.execute("UPDATE txns SET amount_cents = amount_cents * 10 WHERE id='2609-0231'")   # direct edit, flags untouched
    t.commit()
    b3 = engine.build_package(t, P)
    assert engine.package_hash(b3) != engine.package_hash(b1)
    d = engine.diff_packages(b1, b3)
    assert d == [{"id": "2609-0231", "field": "amount_cents", "was": 498000, "now": 4980000}], d
    t.execute("DELETE FROM txns WHERE id='2609-0300'")
    t.commit()
    assert {"id": "2609-0300", "field": "__row__", "was": "present", "now": None} in engine.diff_packages(b1, engine.build_package(t, P))
    t.close()
    # pinned commentary enters the package; routes for the period too
    cc = sqlite3.connect(":memory:"); cc.row_factory = sqlite3.Row
    c.backup(cc)
    cc.execute("INSERT INTO commentary(insight_id,user_id,draft,final,pinned) VALUES ('ins-office-admin','u_controller','d','my words',1)")
    cc.execute("INSERT INTO routes(txn_ids,from_user,to_user,reason,note,status) VALUES (?,?,?,?,?,?)",
               (json.dumps(["2609-0217"]), "u_controller", "u_cfo", "second_opinion", "n", "open"))
    o2 = json.loads(engine.build_package(cc, P))
    assert [i["id"] for i in o2["insights"]] == ["ins-office-admin"] and o2["insights"][0]["commentary_final"] == "my words"
    assert len(o2["routes"]) == 1
    cc.execute("UPDATE txns SET status='approved', resolution=? WHERE id='2609-0300'", (json.dumps({"code": "prepayment", "months": 12}),))
    by = {t["id"]: t for t in json.loads(engine.build_package(cc, P))["txns"]}
    assert by["2609-0300"]["resolution"] == {"code": "prepayment", "months": 12} and by["2609-0231"]["resolution"] is None   # the package carries the decision
    row = one(cc, "SELECT * FROM txns WHERE id='2609-0300'")
    assert engine.effect_cents(row) == -(4625000 - 4625000 // 12) and engine.state(row) == "approved"
    assert engine.effect_cents(one(c, "SELECT * FROM txns WHERE id='2609-0300'")) == 0
    ins = {i["id"]: i for i in engine.insights(cc, P)["insights"]}["ins-cloud-hosting"]
    n = engine.template_narrative(ins)
    assert "12 months" in n["why"] and engine.number_guard(" ".join(n.values()), engine.facts_numbers(ins["facts"]))["unverified"] == []
    assert "as booked" in engine.headline(ins) and "after review adjustments" in engine.headline(ins)


def check_stats(c):
    v = engine.vendor_stats(c, "2609-0231")
    assert v["periods_seen"] == 12 and v["first_seen"] == "2025-09" and 480000 < v["median_cents"] < 515000 and len(v["history"]) == 12
    s = engine.jev_state(c, "2609-0217")
    assert s["code_flags"] == ["duplicate"] and s["txn"]["vendor"] and s["vendor_history"]["periods_seen"] >= 3
    assert engine.risk_label(60) == "high" and engine.risk_label(30) == "med" and engine.risk_label(29) == "low"
    before = [tuple(r) for r in c.execute("SELECT id,flags,risk FROM txns ORDER BY id")]
    engine.flag_period(c, P)
    assert before == [tuple(r) for r in c.execute("SELECT id,flags,risk FROM txns ORDER BY id")]    # idempotent


def check_decisions(ctl, cfo, prep):
    """v2 review decisions over HTTP: cancel, refund request, prepayment, reopen, deck ties. Runs before the batch approve."""
    S = lambda: ctl.call("GET", "/api/summary")[1]
    cloud = lambda: {i["id"]: i for i in ctl.call("GET", "/api/insights")[1]["insights"]}["ins-cloud-hosting"]
    opex = lambda: ctl.call("GET", "/api/deck")[1]["pnl"]["totals"]["opex"]
    wj = planted_ids_by_code["weekend_je"][0]
    s0, o0 = S(), opex()
    assert s0["adjustment_cents"] == 0 and s0["cancelled"] == 0 and s0["decided"] == 0 and o0["adjusted_cents"] == o0["actual_cents"]
    q = ctl.call("GET", "/api/txns?queue=held")[1]
    assert q["rows"] and all(r["held_reasons"] and r["status"] == "pending" for r in q["rows"])
    assert [r["risk"] for r in q["rows"]] == sorted((r["risk"] for r in q["rows"]), reverse=True)
    # cancel: accounting-only entries, controller or cfo, with a note
    st, b = ctl.call("POST", "/api/cancel", {"id": "2609-0300", "note": "not needed"})
    assert st == 403 and "refund" in b["error"], (st, b)
    assert prep.call("POST", "/api/cancel", {"id": wj, "note": "posted in error"})[0] == 403
    assert ctl.call("POST", "/api/cancel", {"id": wj, "note": ""})[0] == 400
    st, b = ctl.call("POST", "/api/cancel", {"id": wj, "note": "posted in error"})
    assert st == 200 and b["txn"]["state"] == b["txn"]["status"] == "cancelled" and b["txn"]["effect_cents"] == -1184000, (st, b)
    s1, o1 = S(), opex()
    assert s1["adjustment_cents"] == -1184000 and (s1["cancelled"], s1["decided"]) == (1, 1) and s1["flagged_pending"] == s0["flagged_pending"] - 1
    assert o1["adjusted_cents"] == o0["adjusted_cents"] - 1184000 and o1["actual_cents"] == o0["actual_cents"]   # the ledger is not changed
    assert ctl.call("POST", "/api/cancel", {"id": wj, "note": "posted in error"})[0] == 409
    # refund request: approves, opens an action for the payments desk
    pay = next(r["id"] for r in ctl.call("GET", "/api/txns?category=Payroll%20%26%20Benefits")[1]["rows"] if r["preparer"] == "u_controller")
    body = {"id": pay, "type": "refund_request", "recover_cents": 100, "note": "overpaid once"}
    assert ctl.call("POST", "/api/action", body)[0] == 403                                   # segregation of duties
    assert prep.call("POST", "/api/action", {**body, "id": "2609-0223"})[0] == 403
    assert ctl.call("POST", "/api/action", {**body, "id": wj})[0] == 403                     # not a cash expense
    assert ctl.call("POST", "/api/action", {**body, "id": "2609-0223", "recover_cents": 490001})[0] == 400
    st, b = ctl.call("POST", "/api/action", {"id": "2609-0223", "type": "refund_request", "recover_cents": 490000, "note": "paid three times"})
    assert st == 200 and b["txn"]["status"] == "approved" and b["txn"]["state"] == "refund_requested" and b["txn"]["effect_cents"] == -490000, (st, b)
    assert (b["route"]["to_user"], b["route"]["reason"], b["route"]["recover_cents"], b["route"]["status"]) == ("u_preparer", "refund_request", 490000, "open")
    assert any(r["reason"] == "refund_request" and r["txn_ids"] == ["2609-0223"] for r in prep.call("GET", "/api/inbox")[1]["open"])
    assert S()["actions_open"] == 1
    # prepayment: booked figure stays, the adjusted one moves, the AI summary is no longer served for the new facts
    acc = next(r["id"] for r in ctl.call("GET", "/api/txns?kind=accounting&status=pending")[1]["rows"])
    pre = {"note": "vendor asked for twelve months up front", "resolution": {"code": "prepayment", "months": 12}}
    assert ctl.call("POST", "/api/approve", {"ids": ["2609-0300"], **pre, "resolution": {"code": "prepayment", "months": 1}})[0] == 400
    assert cfo.call("POST", "/api/approve", {"ids": [acc], **pre})[0] == 403
    assert ctl.call("POST", "/api/approve", {"ids": ["2609-0300"], **pre, "resolution": {"code": "nope"}})[0] == 400
    assert ctl.call("POST", "/api/commentary", {"insight_id": "ins-cloud-hosting", "draft": "cloud note"})[0] == 200
    h0 = cloud()
    assert "adjusted_cents" not in h0["facts"] and h0["facts"]["adjustment_cents"] == 0 and h0["commentary"]["stale"] is False
    assert ctl.call("POST", "/api/approve", {"ids": ["2609-0300"], **pre})[1]["approved"] == ["2609-0300"]
    eff = -(4625000 - 4625000 // 12)
    t = ctl.call("GET", "/api/txns/2609-0300")[1]["txn"]
    assert t["effect_cents"] == eff and t["resolution"] == {"code": "prepayment", "months": 12} and t["state"] == "approved"
    h1 = cloud()
    f = h1["facts"]
    assert f["current_cents"] == h0["facts"]["current_cents"] and f["adjusted_cents"] == f["current_cents"] + eff
    assert abs(f["adjusted_pct_vs_prior"] - 3.9) < 0.11 and f["review_items"][0]["txn_id"] == "2609-0300" and f["review_items"][0]["months"] == 12
    assert h1["facts_hash"] != h0["facts_hash"] and h1["narrative"]["origin"] == "template" and h1["narrative"]["stale"] is False   # AI off: nothing to regenerate
    assert "as booked" in h1["headline"] and h1["commentary"]["stale"] is True and h1["commentary"]["author"] == "Chava"
    st, b = ctl.call("POST", "/api/insights/ins-cloud-hosting/regenerate")
    assert st == 200 and b["narrative"]["origin"] == "template" and b["narrative"]["stale"] is False and "12 months" in b["narrative"]["why"]
    # deck: every sum ties and favourable is by business impact
    d = ctl.call("GET", "/api/deck")[1]
    rows, T, br = d["pnl"]["rows"], d["pnl"]["totals"], d["bridge"]
    for k in ("actual_cents", "adjustment_cents", "adjusted_cents", "prior_cents", "budget_cents", "var_prior_cents", "var_budget_cents"):
        rv, ox = (sum(r[k] for r in rows if r["type"] == t) for t in ("revenue", "expense"))
        assert (T["revenue"][k], T["opex"][k], T["result"][k]) == (rv, ox, rv - ox), k
    assert all(r["adjusted_cents"] == r["actual_cents"] + r["adjustment_cents"] for r in rows + list(T.values()))
    assert br["start"]["value_cents"] + sum(x["delta_cents"] for x in br["steps"]) == br["booked"]["value_cents"] == T["opex"]["actual_cents"]
    assert br["booked"]["value_cents"] + sum(x["delta_cents"] for x in br["adjustments"]) == br["end"]["value_cents"] == T["opex"]["adjusted_cents"]
    assert len(br["steps"]) == 7 and br["start"]["value_cents"] == T["opex"]["prior_cents"]
    assert T["opex"]["adjustment_cents"] == eff - 490000 - 1184000 == d["controls"]["adjustment_total_cents"] == S()["adjustment_cents"]
    assert all((x[k + "_pct"] < 0) == (x[k + "_cents"] < 0) for x in rows + list(T.values()) for k in ("var_prior", "var_budget"))   # sign holds on a negative result
    by = {r["label"]: r for r in rows}
    assert by["Subscription Revenue"]["var_prior_cents"] > 0 and by["Subscription Revenue"]["fav_prior"] is True     # revenue up: favourable
    assert by["Cloud Hosting"]["var_prior_cents"] > 0 and by["Cloud Hosting"]["fav_prior"] is False                  # expense up: not
    assert by["Marketing"]["var_prior_cents"] < 0 and by["Marketing"]["fav_prior"] is True and by["Marketing"]["material"] is True
    assert all(r["fav_prior"] == ((r["var_prior_cents"] > 0) == (r["type"] == "revenue")) for r in rows if r["var_prior_cents"])
    assert [k["key"] for k in d["kpis"]] == ["revenue", "opex", "result", "margin", "headcount", "customers"]
    assert d["kpis"][1]["fav_prior"] is False and d["kpis"][0]["value_cents"] == T["revenue"]["actual_cents"]
    assert all(len(v) == 13 for v in d["trend"].values()) and d["trend"]["opex_cents"][-1] == T["opex"]["actual_cents"]
    assert sorted(i["id"] for i in d["insights"]) == EXPECTED_INSIGHTS
    assert all(len(i["series13"]["actual_cents"]) == 13 and i["headline"] and i["narrative"]["what"] and "text" in i["commentary"] for i in d["insights"])
    assert (d["status"], d["close"], d["prepared_by"], d["period_label"], d["prior_label"]) == ("draft", None, "Chava", "September 2026", "August 2026")
    assert any(o["type"] == "refund_request" and o["recover_cents"] == 490000 and o["owner_name"] == "Alex Kim" for o in d["open_items"])
    assert {a["type"] for a in d["controls"]["adjustments"]} == {"cancelled", "refund_request", "prepayment"} and d["controls"]["cancelled"] == 1
    # reopen: back to pending, effect gone, facts and hash back where they were
    assert prep.call("POST", "/api/reopen", {"id": "2609-0300"})[0] == 403
    st, b = ctl.call("POST", "/api/reopen", {"id": "2609-0300"})
    assert st == 200 and b["txn"]["status"] == "pending" and b["txn"]["effect_cents"] == 0 and b["txn"]["resolution"] is None, (st, b)
    assert ctl.call("POST", "/api/reopen", {"id": "2609-0300"})[0] == 409
    h2 = cloud()
    assert h2["facts_hash"] == h0["facts_hash"] and h2["facts"] == h0["facts"] and h2["commentary"]["stale"] is False
    assert S()["adjustment_cents"] == -490000 - 1184000
    # a reopened refund request is withdrawn for good: the payments desk cannot reply it back to resolved
    mk = next(r["id"] for r in ctl.call("GET", "/api/txns?category=Marketing&status=pending")[1]["rows"] if r["preparer"] == "u_preparer" and r["amount_cents"] > 0)
    rid = ctl.call("POST", "/api/action", {"id": mk, "type": "refund_request", "recover_cents": 1, "note": "paid twice"})[1]["route"]["id"]
    assert ctl.call("POST", f"/api/route/{rid}/reply", {"text": "done", "resolve": True})[0] == 403 and S()["actions_open"] == 2   # the requester cannot close its own refund request
    own = next(r["id"] for r in ctl.call("GET", "/api/txns?kind=accounting&status=pending")[1]["rows"] if r["preparer"] == "u_controller")
    assert ctl.call("POST", "/api/cancel", {"id": own, "note": "posted in error"})[0] == 403                    # segregation of duties on cancel too
    assert ctl.call("POST", "/api/reopen", {"id": mk})[0] == 200 and prep.call("POST", f"/api/route/{rid}/reply", {"text": "done", "resolve": True})[0] == 409
    assert [r["status"] for r in ctl.call("GET", f"/api/txns/{mk}")[1]["routes"]] == ["withdrawn"] and S()["actions_open"] == 1 and S()["adjustment_cents"] == -490000 - 1184000
    return wj


def check_api(tmp):
    """boots app.py on a free port with a temp DB and no secrets, then drives the gates over HTTP"""
    import http.client, http.cookiejar, socket, time, urllib.error, urllib.request
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "TYPESAFE", "HEDERA"))}
    env.update(CLOSE_DB=os.path.join(tmp, "api.db"), LLM="off")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), "--port", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    class Sess:
        def __init__(self, user=None):
            self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            if user:
                assert self.call("POST", "/api/login", {"user_id": user, "password": "demo-" + user.split("_")[1]})[0] == 200

        def call(self, m, path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=m, headers={"Content-Type": "application/json"},
                                         data=json.dumps(body or {}).encode() if m == "POST" else None)
            try:
                with self.op.open(req, timeout=60) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/users", timeout=1); break
            except Exception:
                time.sleep(0.2)
        anon, ctl, cfo, prep = Sess(), Sess("u_controller"), Sess("u_cfo"), Sess("u_preparer")
        assert anon.call("GET", "/api/summary")[0] == 401
        aud, out = Sess("u_auditor"), Sess("u_cfo")
        assert prep.call("POST", "/api/route", {"ids": {"2609-0226": 1}, "to_user": "u_cfo", "reason": "investigation"})[0] == 400   # wrong type, not a 500
        assert prep.call("POST", "/api/batch-check", {"ids": "2609-0226"})[0] == 400
        hc = http.client.HTTPConnection("127.0.0.1", port); hc.request("POST", "/api/login", body=b"not json", headers={"Content-Length": "8"})
        assert hc.getresponse().status == 400
        hc = http.client.HTTPConnection("127.0.0.1", port); hc.request("POST", "/api/cancel", body=b"[" * 100000)   # nesting too deep to parse: 400, not 500
        assert hc.getresponse().status == 400
        assert anon.call("POST", "/api/login", {"user_id": "u_cfo", "password": "d\u00e9mo"})[0] == 401      # non-ASCII password: 401, not 500
        assert aud.call("POST", "/api/commentary", {"insight_id": "ins-office-admin", "draft": "x"})[0] == 403   # auditors read, they do not write
        assert prep.call("POST", "/api/reset")[0] == 403 and prep.call("POST", "/api/demo/tamper")[0] == 403
        assert out.call("POST", "/api/logout")[0] == 200 and out.call("GET", "/api/me")[0] == 401               # logout really ends the session
        assert ctl.call("POST", "/api/commentary", {"insight_id": "ins-office-admin", "draft": ". " * 40})[1]["can_enhance"] is False   # punctuation is not words
        assert anon.call("POST", "/api/login", {"user_id": "u_cfo", "password": "nope"})[0] == 401
        assert ctl.call("GET", "/api/summary")[1]["total"] >= 400
        wj = check_decisions(ctl, cfo, prep)
        st, b = ctl.call("POST", "/api/batch-approve", {"ids": ["2609-0217"]})          # the cheat: planted duplicate
        assert st == 403 and b["approved"] == [] and b["refused"], (st, b)
        assert prep.call("POST", "/api/approve", {"ids": ["2609-0231"], "note": "x"})[0] == 403
        assert prep.call("POST", "/api/batch-approve", {"ids": ["2609-0231"]})[0] == 403
        chk = ctl.call("POST", "/api/batch-check", {})[1]
        assert len(chk["eligible"]) >= 300 and not set(chk["eligible"]) & {i for v in planted_ids_by_code.values() for i in v}
        st, b = ctl.call("POST", "/api/batch-approve", {"ids": chk["eligible"]})
        assert st == 200 and len(b["approved"]) == len(chk["eligible"]) and not b["refused"]
        assert ctl.call("POST", "/api/approve", {"ids": ["2609-0237"], "note": ""})[0] == 400   # flagged: reviewer note required
        assert ctl.call("POST", "/api/approve", {"ids": ["2609-0237"], "note": "supported by signed SOW"})[0] == 200
        rt = ctl.call("POST", "/api/route", {"ids": ["2609-0217"], "to_user": "u_cfo", "reason": "investigation", "note": "paid twice?"})[1]["route"]
        assert len(cfo.call("GET", "/api/inbox")[1]["open"]) == 1
        assert cfo.call("POST", f"/api/route/{rt['id']}/reply", {"text": "confirmed duplicate", "resolve": True})[1]["route"]["status"] == "resolved"
        assert ctl.call("GET", "/api/txns/2609-0217")[1]["txn"]["status"] == "pending"
        iid = "ins-office-admin"
        assert ctl.call("POST", "/api/enhance", {"insight_id": iid, "mode": "live"})[0] == 403      # nothing written yet
        assert ctl.call("POST", "/api/pin", {"insight_id": iid, "pinned": True, "use": "draft"})[0] == 403
        assert ctl.call("POST", "/api/commentary", {"insight_id": iid, "draft": "too short"})[1]["can_enhance"] is False
        assert ctl.call("POST", "/api/enhance", {"insight_id": iid, "mode": "recorded"})[0] == 403
        draft = " ".join(["Office and admin spend rose because we added people and seats, and per head cost stayed flat"] * 2)
        assert ctl.call("POST", "/api/commentary", {"insight_id": iid, "draft": draft})[1]["can_enhance"] is True
        st, e = ctl.call("POST", "/api/enhance", {"insight_id": iid, "mode": "recorded"})
        assert st == 200 and e["origin"] == "recorded" and e["unverified"] == ["35%"] and "<mark" in e["marked_html"], e
        assert ctl.call("POST", "/api/enhance", {"insight_id": iid, "mode": "live"})[1]["origin"] == "template"   # no key: draft returned, never invented text
        st, b = ctl.call("POST", "/api/close/finalize")
        assert st == 409 and b["blockers"], (st, b)                                               # pending and nothing pinned
        assert ctl.call("POST", "/api/enhance", {"insight_id": iid, "mode": "recorded"})[0] == 200          # stored enhanced text now holds the invented 35%
        st, b = ctl.call("POST", "/api/pin", {"insight_id": iid, "pinned": True, "use": "enhanced"})
        assert st == 409 and b["unverified"] == ["35%"], (st, b)
        assert ctl.call("POST", "/api/pin", {"insight_id": iid, "pinned": True, "use": "draft"})[0] == 200
        assert ctl.call("POST", "/api/commentary", {"insight_id": iid, "draft": draft + " edited"})[1]["unpinned"] is True   # editing after a pin unpins
        assert ctl.call("POST", "/api/close/finalize")[0] == 409
        assert ctl.call("POST", "/api/pin", {"insight_id": iid, "pinned": True, "use": "draft"})[0] == 200
        assert ctl.call("POST", "/api/demo/complete-review")[1]["approved"] > 0
        sm = ctl.call("GET", "/api/summary")[1]                                                    # the cancelled entry stays cancelled and does not block
        assert (sm["pending"], sm["cancelled"], sm["readiness_pct"], sm["actions_open"]) == (0, 1, 100, 1) and sm["decided"] == sm["total"]
        assert ctl.call("GET", f"/api/txns/{wj}")[1]["txn"]["state"] == "cancelled" and ctl.call("GET", "/pack.html")[0] == 404
        assert ctl.call("POST", "/api/close/finalize")[0] == 502 and ctl.call("GET", "/api/close/status")[1]["finalized"] is False   # no Hedera creds: refuses, records nothing
        assert ctl.call("POST", "/api/demo/tamper")[1]["now"] == 4980000 and ctl.call("POST", "/api/demo/tamper")[0] == 409
        for path in ("/static/../app.py", "/static/%2e%2e/app.py", "/restored/../close.db"):
            hc = http.client.HTTPConnection("127.0.0.1", port); hc.request("GET", path)
            assert hc.getresponse().status in (401, 404), path       # restored files need a session; static traversal is a plain 404
        assert ctl.call("POST", "/api/reset")[1]["ok"] and ctl.call("GET", "/api/summary")[1]["pending"] >= 400
    finally:
        srv.terminate(); srv.wait(10)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        check_deterministic(tmp)
        path = os.path.join(tmp, "close.db")
        seed.build(path)
        c = connect(path)
        n = check_counts(c)
        flagged = check_planted(c)
        elig, held = check_batch(c)
        ni, nk = check_insights(c)
        check_number_guard(c)
        check_package(c, tmp)
        check_stats(c)
        global planted_ids_by_code
        planted_ids_by_code = planted(c)
        check_api(tmp)
        print(f"txns {P}: {n}, flagged {flagged}, eligible {elig}, held {held}, insights {ni}, kept_out {nk}")
    print("OK")


if __name__ == "__main__":
    main()
