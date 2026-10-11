#!/usr/bin/env python3
"""Close Commenter server: stdlib http.server + sqlite3. Demo auth, synthetic data. Run: python3 app.py [--port 8000]"""
import csv, hashlib, hmac, io, json, mimetypes, os, re, secrets, sqlite3, subprocess, sys, tempfile, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import engine, jev, llm, seed

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("CLOSE_DB") or os.path.join(ROOT, "close.db")
PERIOD = engine.PERIOD
SESSIONS = {}  # ponytail: in-memory demo sessions; lost on restart
LOCK = threading.Lock()  # ponytail: one global writer lock, fine for a demo
FINAL_LOCK = threading.Lock()
FINALIZING = False  # while the Hedera write runs, every other mutation answers 409 so the anchored hash cannot go stale
RESTORED = os.path.join(os.path.dirname(os.path.abspath(DB)), "restored")
MAX_BODY = 256 * 1024
NODE_TIMEOUT = 150
REASONS = {"second_opinion", "investigation", "clarification", "approval"}
APPROVERS = {"controller", "cfo"}
WRITERS = {"controller", "cfo", "preparer"}  # auditor and owner can read, route and reply, not write commentary
TAMPER_ID = "2609-0231"


class Err(Exception):
    def __init__(self, status, msg, **extra):
        self.status, self.body = status, {"error": msg, **extra}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def migrate():
    """A close.db built before v2 lacks two columns; add them so a restart does not need a Reset first."""
    c = db()
    for t, col in (("txns", "resolution"), ("commentary", "facts_hash")):
        if col not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} TEXT")
    c.commit(); c.close()


def audit(c, user, action, target="", detail=""):
    c.execute("INSERT INTO audit(ts,user_id,action,target,detail) VALUES(?,?,?,?,?)", (now(), user, action, target, str(detail)))


def words(t):
    return len(re.findall(r"\w+", t or ""))  # tokens with a letter or digit: "......" does not count as 25 words


def ids_arg(b, what="ids"):
    ids = b.get(what)
    if not (isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids)):
        raise Err(400, f"{what} must be a non-empty list of transaction ids")
    return list(dict.fromkeys(ids))  # de-duplicated, order kept


def text_arg(b, key, limit):
    v = b.get(key, "")
    if not isinstance(v, str) or len(v) > limit:
        raise Err(400, f"{key} must be text of at most {limit} characters")
    return v


def txn_dict(r):
    d = {k: r[k] for k in ("id", "date", "vendor", "category", "amount_cents", "kind", "source", "status", "risk",
                           "preparer", "approver", "approve_note", "memo", "source_ref")}
    d["risk_label"] = engine.risk_label(r["risk"])
    d["flags"] = json.loads(r["flags"] or "[]")
    d["jev"] = json.loads(r["jev"]) if r["jev"] else None
    d.update(resolution=engine.resolution(r), effect_cents=engine.effect_cents(r), state=engine.state(r))
    return d


def cash_expense(r):
    return r["kind"] == "activity" and engine.CATEGORIES.get(r["category"]) == "expense"


def period_txn(c, tid):
    r = c.execute("SELECT * FROM txns WHERE id=? AND period=?", (tid, PERIOD)).fetchone() if isinstance(tid, str) else None
    if not r:
        raise Err(404, "No such transaction in the closing period")
    return r


def need_note(b):
    note = text_arg(b, "note", 1000).strip()
    if len(note) < 5:
        raise Err(400, "A note (5+ characters) is required")
    return note


def route_dict(c, r):
    names = {u["id"]: u["name"] for u in c.execute("SELECT id,name FROM users")}
    ids = json.loads(r["txn_ids"])
    q = ",".join("?" * len(ids))
    rows = c.execute(f"SELECT * FROM txns WHERE id IN ({q})", ids).fetchall()
    txns = [dict(vendor=t["vendor"], id=t["id"], amount_cents=t["amount_cents"]) for t in rows]
    rec = next(((engine.resolution(t) or {}).get("recover_cents") for t in rows), None) if r["reason"] == "refund_request" else None
    return {"id": r["id"], "txn_ids": ids, "from_user": r["from_user"], "from_name": names.get(r["from_user"]),
            "to_user": r["to_user"], "to_name": names.get(r["to_user"]), "reason": r["reason"], "note": r["note"],
            "status": r["status"], "reply": r["reply"], "ts": r["ts"], "reply_ts": r["reply_ts"], "txns": txns,
            "recover_cents": rec}


def finalized(c):
    return c.execute("SELECT 1 FROM closes WHERE period=?", (PERIOD,)).fetchone() is not None


def need_open(c):
    if finalized(c):
        raise Err(409, "Period is finalized and locked")
    if FINALIZING:
        raise Err(409, "The close is being anchored on Hedera; try again in a few seconds")


def need_role(u, roles, what):
    if u["role"] not in roles:
        raise Err(403, f"Role {u['role']} cannot {what}")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def chain_topic():
    try:
        return json.load(open(os.path.join(ROOT, "chain.json"))).get("topic_id")
    except Exception:
        return None


def node(args, tmo=NODE_TIMEOUT):
    try:
        p = subprocess.run(["node", os.path.join(ROOT, "chain.mjs"), *args], capture_output=True, text=True, timeout=tmo, cwd=ROOT)
    except subprocess.TimeoutExpired:
        raise Err(502, f"Hedera step timed out after {tmo} s", hint="Venue network may block gRPC (ports 50211/50212); use a hotspot or the recorded demo")
    out = (p.stdout or "").strip().splitlines()
    try:
        res = json.loads(out[-1]) if out else {}
    except ValueError:
        res = {}
    if p.returncode != 0 or res.get("error"):
        msg = str(res.get("error") or (p.stderr or "")[-300:] or "no output")
        if "no complete begin+commit" in msg:
            raise Err(404, "That close was not found on the topic: " + msg)
        if "sha256 mismatch" in msg or "decrypt" in msg or "encryption key" in msg:
            raise Err(409, "Chain data does not match: " + msg)
        raise Err(502, "Hedera step failed: " + msg,
                  hint="Venue network may block gRPC (ports 50211/50212); use a hotspot or the recorded demo" if args[0] in ("publish", "init") else None)
    return res


def close_dict(r):
    return {"close_id": r["close_id"], "period": r["period"], "hash": r["hash"], "hash8": r["hash"][:8],
            "prev_hash8": (r["prev_hash"] or "")[:8], "topic_id": r["topic_id"], "messages": r["messages"],
            "bytes": r["bytes"], "tier": r["tier"], "links": json.loads(r["links"] or "[]"), "closed_by": r["closed_by"], "ts": r["ts"]}


def latest_close(c):
    return c.execute("SELECT * FROM closes WHERE period=? ORDER BY ts DESC LIMIT 1", (PERIOD,)).fetchone()


# ---------- handlers: (ctx) -> obj ; ctx = dict(c, u, body, q, m) ----------
def h_login(x):
    uid, pw = x["body"].get("user_id"), x["body"].get("password", "")
    r = x["c"].execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if isinstance(uid, str) else None
    if not r or not isinstance(pw, str) or not hmac.compare_digest(str(r["password"]).encode(), pw.encode()):
        raise Err(401, "Wrong user or password")
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = r["id"]
    x["cookie"] = sid
    return {"user": {"id": r["id"], "name": r["name"], "role": r["role"]}}


def h_logout(x):
    SESSIONS.pop(x.get("sid"), None)
    x["clear_cookie"] = True
    return {"ok": True}


def h_users(x):
    return [{"id": r["id"], "name": r["name"], "role": r["role"], "hint": r["hint"], "demo_password": r["password"]}
            for r in x["c"].execute("SELECT * FROM users ORDER BY rowid")]


def h_me(x):
    u = x["u"]
    return {"user": {"id": u["id"], "name": u["name"], "role": u["role"]}, "period": PERIOD, "company": "Northgate Labs",
            "demo": True, "ai": {"system2": llm.provider(), "jev": jev.available()},
            "chain": {"configured": bool(chain_topic() and os.environ.get("HEDERA_ACCOUNT_ID")), "topic_id": chain_topic()}}


def h_summary(x):
    c = x["c"]
    rows = c.execute("SELECT * FROM txns WHERE period=?", (PERIOD,)).fetchall()
    n = lambda f: sum(1 for r in rows if f(r))
    done = lambda r: r["status"] in engine.DECIDED
    total, appr, canc = len(rows), n(lambda r: r["status"] == "approved"), n(lambda r: r["status"] == "cancelled")
    return {"period": PERIOD, "total": total, "approved": appr, "cancelled": canc, "decided": appr + canc,
            "routed": n(lambda r: r["status"] == "routed"),
            "pending": n(lambda r: r["status"] == "pending"), "flagged": n(lambda r: r["flags"] != "[]"),
            "flagged_pending": n(lambda r: r["flags"] != "[]" and not done(r)),
            "at_risk_cents": sum(r["amount_cents"] for r in rows if not done(r) and r["risk"] >= 30),
            "actions_open": c.execute("SELECT COUNT(*) FROM routes WHERE status='open' AND reason='refund_request'").fetchone()[0],
            "adjustment_cents": sum(engine.effect_cents(r) for r in rows),
            "by_kind": {k: n(lambda r, k=k: r["kind"] == k) for k in ("activity", "accounting")},
            "readiness_pct": round(100 * (appr + canc) / total) if total else 0, "finalized": finalized(c)}


def h_txns(x):
    q = x["q"]
    if q.get("queue") == "held":  # what the batch gate holds for this reviewer, highest risk first
        held = {h["id"]: h["reasons"] for h in engine.batch_check(x["c"], None, x["u"]["id"])["held"]}
        rows = [r for r in x["c"].execute("SELECT * FROM txns WHERE period=? AND status='pending' ORDER BY risk DESC, amount_cents DESC, id", (PERIOD,))
                if r["id"] in held]
        return {"rows": [{**txn_dict(r), "held_reasons": held[r["id"]]} for r in rows], "total": len(rows)}
    where, args = ["period=?"], [PERIOD]
    for col in ("kind", "status", "category"):
        if q.get(col):
            where.append(f"{col}=?"); args.append(q[col])
    if q.get("risk") == "high": where.append("risk>=60")
    elif q.get("risk") == "med": where.append("risk>=30 AND risk<60")
    elif q.get("risk") == "low": where.append("risk<30")
    if q.get("q"):
        like = f"%{q['q']}%"
        where.append("(vendor LIKE ? OR memo LIKE ? OR id LIKE ? OR category LIKE ? OR CAST(amount_cents/100 AS TEXT) LIKE ?)")
        args += [like] * 5
    sql = " AND ".join(where)
    total = x["c"].execute(f"SELECT COUNT(*) FROM txns WHERE {sql}", args).fetchone()[0]
    try:
        lim, off = max(1, min(int(q.get("limit", 500)), 1000)), max(0, int(q.get("offset", 0)))
    except ValueError:
        raise Err(400, "limit and offset must be integers")
    rows = x["c"].execute(f"SELECT * FROM txns WHERE {sql} ORDER BY risk DESC, amount_cents DESC, id LIMIT ? OFFSET ?", args + [lim, off])
    return {"rows": [txn_dict(r) for r in rows], "total": total}


def h_txn(x):
    c, tid = x["c"], x["m"].group(1)
    r = c.execute("SELECT * FROM txns WHERE id=?", (tid,)).fetchone()
    if not r:
        raise Err(404, "No such transaction")
    routes = [route_dict(c, o) for o in c.execute("SELECT * FROM routes WHERE txn_ids LIKE ?", (f'%"{tid}"%',))]
    aud = [{"ts": a["ts"], "user_id": a["user_id"], "action": a["action"], "detail": a["detail"]}
           for a in c.execute("SELECT * FROM audit WHERE target=? ORDER BY id", (tid,))]
    return {"txn": txn_dict(r), "vendor": engine.vendor_stats(c, tid), "category": engine.category_stats(c, tid), "routes": routes, "audit": aud}


def approve_rows(c, u, ids, note, batch=False, resolution=None):
    ok, refused = [], []
    for i in ids:
        r = c.execute("SELECT * FROM txns WHERE id=?", (i,)).fetchone()
        if not r or r["period"] != PERIOD:
            refused.append({"id": i, "reasons": ["not in the closing period"], "codes": ["not_found"]})
        elif r["status"] != "pending":
            refused.append({"id": i, "reasons": [f"status is {r['status']}"], "codes": ["not_pending"]})
        elif r["preparer"] == u["id"]:
            refused.append({"id": i, "reasons": ["segregation of duties: you prepared this entry"], "codes": ["sod"]})
        else:
            c.execute("UPDATE txns SET status='approved', approver=?, approve_note=?, approve_ts=?, resolution=? WHERE id=?",
                      (u["id"], note, now(), json.dumps(resolution, sort_keys=True) if resolution else None, i))
            ok.append(i)
    return ok, refused


def h_approve(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_role(u, APPROVERS, "approve"); need_open(c)
    ids, note = ids_arg(b), text_arg(b, "note", 1000).strip()
    if len(ids) != 1:
        raise Err(400, "Approve one transaction at a time; use batch-approve for many")
    r = c.execute("SELECT * FROM txns WHERE id=?", (ids[0],)).fetchone()
    if r and engine.risk_label(r["risk"]) != "low" and len(note) < 5:
        raise Err(400, "A reviewer note (5+ characters) is required to approve a flagged transaction")
    res = b.get("resolution")
    if res is not None:
        if not isinstance(res, dict) or res.get("code") not in ("supported", "prepayment", "one_time", "timing"):
            raise Err(400, "resolution.code must be supported, prepayment, one_time or timing")
        m, res = res.get("months"), {"code": res["code"]}
        if res["code"] == "prepayment":
            if not (isinstance(m, int) and not isinstance(m, bool) and 2 <= m <= 60):
                raise Err(400, "A prepayment needs a whole number of months from 2 to 60")
            if r and not cash_expense(r):
                raise Err(403, "Only a cash expense can be spread as a prepayment")
            res["months"] = m
    ok, refused = approve_rows(c, u, ids, note, resolution=res)
    for i in ok:
        audit(c, u["id"], "approve", i, f"{note} [{res['code']}{' ' + str(res['months']) + ' months' if 'months' in res else ''}]" if res else note)
    c.commit()
    return {"approved": ok, "refused": refused}


def h_cancel(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_role(u, APPROVERS, "cancel an entry"); need_open(c)
    r = period_txn(c, b.get("id"))
    if r["kind"] != "accounting":
        raise Err(403, "Only accounting-only entries can be cancelled. For a cash transaction, request a refund.")
    if r["preparer"] == u["id"]:  # same rule as approve and refund: a cancel moves the figure by the whole amount
        raise Err(403, "Segregation of duties: you prepared this entry")
    if r["status"] != "pending":
        raise Err(409, f"{r['id']} is {r['status']}, not pending")
    note = need_note(b)
    c.execute("UPDATE txns SET status='cancelled', approver=?, approve_note=?, approve_ts=?, resolution=NULL WHERE id=?", (u["id"], note, now(), r["id"]))
    audit(c, u["id"], "cancel", r["id"], note)
    c.commit()
    return {"txn": txn_dict(period_txn(c, r["id"]))}


def h_action(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_role(u, APPROVERS, "request a refund"); need_open(c)
    if b.get("type") != "refund_request":
        raise Err(400, "type must be refund_request")
    r = period_txn(c, b.get("id"))
    if not cash_expense(r):
        raise Err(403, "A refund can only be requested on a cash expense")
    if r["preparer"] == u["id"]:
        raise Err(403, "Segregation of duties: you prepared this entry")
    if r["status"] != "pending":
        raise Err(409, f"{r['id']} is {r['status']}, not pending")
    rec, note = b.get("recover_cents"), need_note(b)
    if not (isinstance(rec, int) and not isinstance(rec, bool) and 0 < rec <= r["amount_cents"]):
        raise Err(400, "recover_cents must be a whole number of cents, above 0 and at most the amount paid")
    c.execute("UPDATE txns SET status='approved', approver=?, approve_note=?, approve_ts=?, resolution=? WHERE id=?",
              (u["id"], note, now(), json.dumps({"code": "refund_request", "recover_cents": rec}, sort_keys=True), r["id"]))
    cur = c.execute("INSERT INTO routes(txn_ids,from_user,to_user,reason,note,ts) VALUES(?,?,?,?,?,?)",   # stays open; the txn is decided
                    (json.dumps([r["id"]]), u["id"], engine.PAYMENTS_USER, "refund_request", note, now()))
    audit(c, u["id"], "refund_request", r["id"], f"recover {rec} cents -> {engine.PAYMENTS_USER}: {note}")
    c.commit()
    return {"txn": txn_dict(period_txn(c, r["id"])),
            "route": route_dict(c, c.execute("SELECT * FROM routes WHERE id=?", (cur.lastrowid,)).fetchone())}


def h_reopen(x):
    c, u = x["c"], x["u"]
    need_role(u, APPROVERS, "reopen a decision"); need_open(c)
    r = period_txn(c, x["body"].get("id"))
    if r["status"] not in engine.DECIDED:
        raise Err(409, f"{r['id']} is {r['status']}; only an approved or cancelled entry can be reopened")
    c.execute("UPDATE txns SET status='pending', approver=NULL, approve_note=NULL, approve_ts=NULL, resolution=NULL WHERE id=?", (r["id"],))
    c.execute("UPDATE routes SET status='withdrawn' WHERE status='open' AND reason='refund_request' AND txn_ids LIKE ?", (f'%"{r["id"]}"%',))
    audit(c, u["id"], "reopen", r["id"], f"was {engine.state(r)}")
    c.commit()
    return {"txn": txn_dict(period_txn(c, r["id"]))}


def h_batch_check(x):
    return engine.batch_check(x["c"], ids_arg(x["body"]) if x["body"].get("ids") is not None else None, x["u"]["id"])


def h_batch_approve(x):
    c, u = x["c"], x["u"]
    need_role(u, APPROVERS, "approve"); need_open(c)
    ids = ids_arg(x["body"])
    chk = engine.batch_check(c, ids, u["id"])  # the server decides, never the client
    ok, refused = approve_rows(c, u, chk["eligible"], "batch gate passed", True)
    refused = chk["held"] + refused
    for i in ok:
        audit(c, u["id"], "batch_approve", i, "gate passed")
    c.commit()
    if refused and not ok:
        raise Err(403, "Batch gate refused every id", approved=[], refused=refused)
    return {"approved": ok, "refused": refused}


def h_route(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_open(c)
    ids, to, note = ids_arg(b), b.get("to_user"), text_arg(b, "note", 1000)
    if b.get("reason") not in REASONS or not isinstance(to, str) or not c.execute("SELECT 1 FROM users WHERE id=?", (to,)).fetchone() or to == u["id"]:
        raise Err(400, "Need ids, a different recipient and a reason: " + ", ".join(sorted(REASONS)))
    for i in ids:
        r = c.execute("SELECT status FROM txns WHERE id=? AND period=?", (i, PERIOD)).fetchone()
        if not r or r["status"] != "pending":
            raise Err(409, f"{i} is not pending")
    cur = c.execute("INSERT INTO routes(txn_ids,from_user,to_user,reason,note,ts) VALUES(?,?,?,?,?,?)",
                    (json.dumps(ids), u["id"], to, b["reason"], note, now()))
    c.executemany("UPDATE txns SET status='routed' WHERE id=?", [(i,) for i in ids])
    audit(c, u["id"], "route", ",".join(ids), f"{b['reason']} -> {to}: {note}")
    c.commit()
    return {"route": route_dict(c, c.execute("SELECT * FROM routes WHERE id=?", (cur.lastrowid,)).fetchone())}


def h_inbox(x):
    c, u = x["c"], x["u"]
    f = lambda col: [route_dict(c, r) for r in c.execute(f"SELECT * FROM routes WHERE {col}=? ORDER BY id DESC", (u["id"],))]
    return {"open": [r for r in f("to_user") if r["status"] == "open"], "sent": f("from_user")}


def h_reply(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_open(c)
    r = c.execute("SELECT * FROM routes WHERE id=?", (x["m"].group(1),)).fetchone()
    if not r:
        raise Err(404, "No such route")
    if u["id"] not in (r["to_user"], r["from_user"]):
        raise Err(403, "Not your route")
    if r["status"] == "withdrawn":  # the decision behind it was reopened; a reply must not turn it back into a resolved refund
        raise Err(409, "This request was withdrawn when the decision was reopened")
    if r["reason"] == "refund_request" and b.get("resolve") and u["id"] != r["to_user"]:  # the requester withdraws by reopening
        raise Err(403, "Only the payments desk can close a refund request")
    text = text_arg(b, "text", 1000)
    was_open = r["status"] == "open"
    c.execute("UPDATE routes SET reply=?, reply_ts=?, status=? WHERE id=?",
              (text, now(), "resolved" if b.get("resolve") else r["status"], r["id"]))
    if b.get("resolve") and was_open:
        for i in json.loads(r["txn_ids"]):
            if not c.execute("SELECT 1 FROM routes WHERE status='open' AND id!=? AND txn_ids LIKE ?", (r["id"], f'%"{i}"%')).fetchone():
                c.execute("UPDATE txns SET status='pending' WHERE id=? AND status='routed'", (i,))
    audit(c, u["id"], "route_reply", r["txn_ids"], text)
    c.commit()
    return {"route": route_dict(c, c.execute("SELECT * FROM routes WHERE id=?", (r["id"],)).fetchone())}


def cache(name):
    try:
        return json.load(open(os.path.join(ROOT, "cache", name)))
    except Exception:
        return {}


def commentary_view(c, ins):
    r = c.execute("SELECT m.*, u.name AS author FROM commentary m LEFT JOIN users u ON u.id=m.user_id WHERE insight_id=?", (ins["id"],)).fetchone()
    draft, enh = (r["draft"] or "") if r else "", (r["enhanced"] or "") if r else ""
    g = engine.number_guard(enh, engine.facts_numbers(ins["facts"]), draft) if enh else {"unverified": [], "marked_html": ""}
    return {"draft": draft, "enhanced": enh, "final": (r["final"] or "") if r else "", "pinned": bool(r and r["pinned"]),
            "words": words(draft), "can_enhance": words(draft) >= 25, "unverified": g["unverified"], "marked_html": g["marked_html"],
            "author": r["author"] if r else None,
            "stale": bool(r and draft and r["facts_hash"] and r["facts_hash"] != engine.facts_hash(ins))}  # written against older facts


def dress(c, ins, cached):
    """Narrative and commentary for one insight. AI text is served only while its facts hash equals the current one."""
    h = engine.facts_hash(ins)
    ent = cached.get(ins["id"]) or {}
    m = c.execute("SELECT value FROM meta WHERE key=?", ("nar:" + ins["id"],)).fetchone()
    cands = [json.loads(m[0])] if m else []
    if ent.get("narrative"):
        cands.append({**ent["narrative"], "facts_hash": ent.get("facts_hash"), "chart_type": (ent.get("chart") or {}).get("type")})
    ai = next((n for n in cands if n.get("facts_hash") == h), None)
    if ai:
        ins["narrative"] = {**{k: ai.get(k, "") for k in ("what", "context", "why")}, "origin": "ai", "stale": False}
        if ai.get("chart_type") in ("bar", "line"):
            ins["chart"]["type"] = ai["chart_type"]
    else:  # facts moved on (a review decision) or nothing generated: code-written text, and say an AI summary could be made
        ins["narrative"] = {**engine.template_narrative(ins), "origin": "template", "stale": llm.provider() != "off"}
    ins["chart"].setdefault("origin", "ai" if ai else "template")
    ins["facts_hash"], ins["headline"] = h, engine.headline(ins)
    ins["commentary"] = commentary_view(c, ins)
    return ins


def h_insights(x):
    c, cached = x["c"], cache("insights.json")
    data = engine.insights(c, PERIOD)
    for ins in data["insights"]:
        dress(c, ins, cached)
    return data


def h_regenerate(x):
    """One Claude call for one insight. Runs outside LOCK (slow); only the meta write takes it."""
    c, u = x["c"], x["u"]
    need_role(u, WRITERS, "regenerate a summary"); need_open(c)
    ins = find_insight(c, x["m"].group(1))
    h = engine.facts_hash(ins)
    n = llm.narrate([ins])[ins["id"]]  # number-guarded inside; template on any failure
    nar = {k: n[k] for k in ("what", "context", "why")}
    if n.get("origin") != "ai":
        return {"narrative": {**nar, "origin": "template", "stale": llm.provider() != "off"}}
    with LOCK:
        c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("nar:" + ins["id"], json.dumps({"facts_hash": h, **nar, "chart_type": n.get("chart_type")})))
        audit(c, u["id"], "regenerate", ins["id"], h)
        c.commit()
    return {"narrative": {**nar, "origin": "ai", "stale": engine.facts_hash(find_insight(c, ins["id"])) != h}}


def h_deck(x):
    c, u, cached = x["c"], x["u"], cache("insights.json")
    d = engine.deck(c, PERIOD)
    for ins in d["insights"]:
        cv = dress(c, ins, cached)["commentary"]
        ins["commentary"] = {"text": cv["final"] if cv["pinned"] else cv["draft"], "author": cv["author"], "pinned": cv["pinned"], "stale": cv["stale"]}
    cl = latest_close(c)
    names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM users")}
    d.update(status="final" if cl else "draft", prepared_by=u["name"],
             close={**{k: close_dict(cl)[k] for k in ("close_id", "hash", "hash8", "prev_hash8", "topic_id", "ts", "tier")},
                    "closed_by_name": names.get(cl["closed_by"], cl["closed_by"])} if cl else None)
    return d


def find_insight(c, iid):
    for i in engine.insights(c, PERIOD)["insights"]:
        if i["id"] == iid:
            return i
    raise Err(404, "No such insight")


def h_commentary(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_role(u, WRITERS, "write commentary"); need_open(c); ins = find_insight(c, b.get("insight_id"))
    draft = text_arg(b, "draft", 5000)
    was = c.execute("SELECT draft, pinned FROM commentary WHERE insight_id=?", (b["insight_id"],)).fetchone()
    if was and was["draft"] == draft:  # autosave and flush-before-enhance send the same text: nothing to invalidate
        return {"words": words(draft), "can_enhance": words(draft) >= 25, "unpinned": False, "changed": False}
    c.execute("INSERT INTO commentary(insight_id,user_id,draft,ts,facts_hash) VALUES(?,?,?,?,?) ON CONFLICT(insight_id) DO UPDATE SET draft=excluded.draft,user_id=excluded.user_id,ts=excluded.ts,facts_hash=excluded.facts_hash,pinned=0,enhanced=NULL,final=NULL",
              (b["insight_id"], u["id"], draft, now(), engine.facts_hash(ins)))  # editing changes what was reviewed: it unpins and drops the old enhancement, so the pack and the anchor never carry stale text
    audit(c, u["id"], "commentary", b["insight_id"], f"{words(draft)} words")
    c.commit()
    return {"words": words(draft), "can_enhance": words(draft) >= 25, "unpinned": bool(was and was["pinned"]), "changed": True}


def h_enhance(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_role(u, WRITERS, "enhance commentary"); need_open(c)
    ins = find_insight(c, b.get("insight_id"))
    r = c.execute("SELECT draft FROM commentary WHERE insight_id=?", (ins["id"],)).fetchone()
    draft = (r["draft"] if r else "") or ""
    if words(draft) < 25:
        raise Err(403, "Write your own commentary first (25+ words). Enhance only polishes what you wrote.")
    if b.get("mode") == "recorded":
        text, origin = (cache("enhance_recorded.json").get(ins["id"]) or {}).get("enhanced"), "recorded"
        if not text:
            raise Err(404, "No recorded example for this insight")
    else:
        text, origin = llm.enhance(draft, ins["facts"]), "live"
        if not text:
            text, origin = draft, "template"
    g = engine.number_guard(text, engine.facts_numbers(ins["facts"]), draft)
    c.execute("UPDATE commentary SET enhanced=? WHERE insight_id=?", (text, ins["id"]))
    audit(c, u["id"], "enhance", ins["id"], f"{origin}, {len(g['unverified'])} unverified")
    c.commit()
    return {"enhanced": text, "marked_html": g["marked_html"], "unverified": g["unverified"], "origin": origin}


def h_pin(x):
    c, u, b = x["c"], x["u"], x["body"]
    need_role(u, WRITERS, "pin commentary"); need_open(c); ins = find_insight(c, b.get("insight_id"))
    r = c.execute("SELECT draft, enhanced FROM commentary WHERE insight_id=?", (b["insight_id"],)).fetchone()
    if b.get("pinned") and (not r or words(r["draft"]) < 25):
        raise Err(403, "Write your own commentary (25+ words) before pinning")
    final = (r["enhanced"] if b.get("use") == "enhanced" and r["enhanced"] else r["draft"]) if r else ""
    if b.get("pinned") and b.get("use") == "enhanced":
        bad = engine.number_guard(final, engine.facts_numbers(ins["facts"]), r["draft"])["unverified"]
        if bad:
            raise Err(409, "The enhanced text contains figures that are not in the facts or your draft: " + ", ".join(bad) + ". Pin your own draft or edit it.", unverified=bad)
    c.execute("UPDATE commentary SET pinned=?, final=?, facts_hash=CASE WHEN ? THEN ? ELSE facts_hash END WHERE insight_id=?",   # pinning signs off the current facts
              (1 if b.get("pinned") else 0, final, 1 if b.get("pinned") else 0, engine.facts_hash(ins), b["insight_id"]))
    audit(c, u["id"], "pin" if b.get("pinned") else "unpin", b["insight_id"], b.get("use", "draft"))
    c.commit()
    return {"ok": True}


def h_audit(x):
    try:
        n = max(1, min(int(x["q"].get("limit", 100)), 1000))
    except ValueError:
        raise Err(400, "limit must be an integer")
    return [{"ts": a["ts"], "user_id": a["user_id"], "action": a["action"], "target": a["target"], "detail": a["detail"]}
            for a in x["c"].execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (n,))]


def anchor(c, period, user_id):
    """Package -> encrypt -> HCS topic via chain.mjs, then record the close. Raises Err(502) if Hedera is unreachable."""
    topic = chain_topic()
    if not topic: raise Err(502, "No Hedera topic configured (run: op run --env-file=.env.op -- node chain.mjs init)")
    pkg = engine.build_package(c, period)
    prev = c.execute("SELECT hash FROM closes WHERE period<? ORDER BY period DESC, ts DESC LIMIT 1", (period,)).fetchone()
    close_id = f"CLS-{period}-{secrets.token_hex(3)}"
    tier = "hash-only" if os.environ.get("CHAIN_TIER") == "hash-only" else "full"
    with tempfile.TemporaryDirectory() as d:
        pf, out = os.path.join(d, "pkg.json"), os.path.join(d, "res.json")
        open(pf, "wb").write(pkg)
        args = ["publish", "--package", pf, "--close-id", close_id, "--period", period, "--hash", sha(pkg),
                "--prev", prev["hash"] if prev else "", "--closed-by", user_id, "--out", out]
        if tier == "hash-only": args.append("--hash-only")
        node(args)
        res = json.load(open(out))
    c.execute("INSERT INTO closes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (close_id, period, sha(pkg), prev["hash"] if prev else "", topic, res.get("messages"), res.get("bytes_encrypted"),
               tier, json.dumps(res.get("links", [])), user_id, now()))
    audit(c, user_id, "finalize", close_id, sha(pkg)[:16])
    c.commit()
    if period < PERIOD:  # earlier closes are kept in cache/closes.json so seed.build and Reset restore the link
        f = os.path.join(ROOT, "cache", "closes.json")
        rows = json.load(open(f)) if os.path.exists(f) else []
        rows.append(dict(c.execute("SELECT * FROM closes WHERE close_id=?", (close_id,)).fetchone()))
        json.dump(rows, open(f, "w"), indent=1)


def h_finalize(x):
    c, u = x["c"], x["u"]
    need_role(u, APPROVERS, "finalize the close")
    blockers = []
    if finalized(c): raise Err(409, "Already finalized", blockers=["This period is already anchored"])
    n = c.execute("SELECT COUNT(*) FROM txns WHERE period=? AND status NOT IN ('approved','cancelled')", (PERIOD,)).fetchone()[0]
    if n: blockers.append(f"{n} transactions are not approved or cancelled yet")
    if not any(words(r["draft"]) >= 25 and words(r["final"]) >= 25 for r in c.execute("SELECT draft, final FROM commentary WHERE pinned=1")):
        blockers.append("Pin at least one insight with your own commentary (25+ words)")
    if blockers: raise Err(409, "Close is not ready", blockers=blockers)
    global FINALIZING
    if not FINAL_LOCK.acquire(blocking=False): raise Err(409, "Already finalizing")
    try:
        FINALIZING = True
        anchor(c, PERIOD, u["id"])
    finally:
        FINALIZING = False
        FINAL_LOCK.release()
    return {"close": close_dict(latest_close(c))}


def h_close_status(x):
    c = x["c"]
    cur = latest_close(c)
    return {"finalized": bool(cur), "close": close_dict(cur) if cur else None,
            "history": [close_dict(r) for r in c.execute("SELECT * FROM closes ORDER BY period, ts")]}


def fetch_chain(cl):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "pkg.json")
        res = node(["fetch", "--topic", cl["topic_id"], "--close-id", cl["close_id"], "--out", out])
        return res, (open(out, "rb").read() if os.path.exists(out) else b"")


def h_verify(x):
    c = x["c"]
    cl = latest_close(c)
    if not cl: raise Err(409, "Nothing anchored yet")
    t0 = time.time()
    res, chain_b = fetch_chain(cl)
    live = engine.build_package(c, PERIOD)
    chain_hash = res.get("sha256") or sha(chain_b)
    begin = res.get("begin") or {}
    record_ok = begin.get("sha256", cl["hash"]) == cl["hash"] and (begin.get("prev") or "") == (cl["prev_hash"] or "")   # our closes row vs the chain
    changed = engine.diff_packages(chain_b, live) if chain_b and sha(live) != chain_hash else []
    ok = sha(live) == chain_hash and record_ok
    audit(c, x["u"]["id"], "verify", cl["close_id"], "match" if ok else f"{len(changed)} changes"); c.commit()
    return {"ok": ok, "live_hash8": sha(live)[:8], "chain_hash8": chain_hash[:8], "changed": changed, "record_ok": record_ok,
            "ms": int((time.time() - t0) * 1000), "via": "mirror-node"}


def h_restore(x):
    c = x["c"]
    cl = latest_close(c)
    if not cl: raise Err(409, "Nothing anchored yet")
    res, chain_b = fetch_chain(cl)
    if not chain_b: raise Err(409, "This close is hash-only: no payload on chain to restore")
    d = RESTORED
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"{PERIOD}.json"), "wb").write(chain_b)
    pkg = json.loads(chain_b)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "date", "vendor", "category", "amount", "kind", "status", "approver", "approve_note", "source_ref"])
    safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in "=+-@" else v   # CSV formula injection guard
    for t in pkg["txns"]:
        w.writerow([safe(v) for v in (t["id"], t["date"], t["vendor"], t["category"], f"{t['amount_cents'] / 100:.2f}", t["kind"], t["status"], t["approver"], t["approve_note"], t["source_ref"])])
    open(os.path.join(d, f"{PERIOD}.csv"), "w").write(buf.getvalue())
    audit(c, x["u"]["id"], "restore", cl["close_id"], f"{len(pkg['txns'])} txns"); c.commit()
    return {"ok": True, "hash_ok": sha(chain_b) == cl["hash"], "txns": len(pkg["txns"]), "bytes": len(chain_b),
            "files": {"json": f"/restored/{PERIOD}.json", "csv": f"/restored/{PERIOD}.csv"}}


def h_tamper(x):
    c = x["c"]
    need_role(x["u"], APPROVERS, "simulate an external edit")
    r = c.execute("SELECT amount_cents FROM txns WHERE id=?", (TAMPER_ID,)).fetchone()
    if not r or r["amount_cents"] != 498000: raise Err(409, "Already edited; press Reset to start over")
    c.execute("UPDATE txns SET amount_cents=amount_cents*10 WHERE id=?", (TAMPER_ID,))
    audit(c, x["u"]["id"], "external_edit", TAMPER_ID, "simulated QuickBooks edit after close")
    c.commit()
    return {"id": TAMPER_ID, "field": "amount_cents", "was": 498000, "now": 4980000}


def h_complete_review(x):
    """Labelled demo shortcut: approve everything still open as if reviewed earlier, so the live demo can reach Finalize."""
    c, u = x["c"], x["u"]
    need_role(u, APPROVERS, "use the demo shortcut"); need_open(c)
    n_routes = c.execute("UPDATE routes SET status='resolved', reply=COALESCE(reply,'Demo shortcut'), reply_ts=? WHERE status='open' AND reason!='refund_request'", (now(),)).rowcount   # refund requests stay with the payments desk
    rows = c.execute("SELECT id, preparer FROM txns WHERE period=? AND status NOT IN ('approved','cancelled')", (PERIOD,)).fetchall()
    for r in rows:
        who = "u_cfo" if r["preparer"] == "u_controller" else "u_controller"
        c.execute("UPDATE txns SET status='approved', approver=?, approve_note=?, approve_ts=? WHERE id=?", (who, "Demo shortcut: reviewed earlier", now(), r["id"]))
    audit(c, u["id"], "demo_shortcut", "all", f"{len(rows)} txns approved, {n_routes} routes resolved")
    c.commit()
    return {"approved": len(rows), "resolved_routes": n_routes}


def h_reset(x):
    need_role(x["u"], APPROVERS, "reset the demo")
    keep = x["c"].execute("SELECT * FROM closes WHERE period<?", (PERIOD,)).fetchall()
    new = DB + ".new"
    for f in (new, new + "-journal"):
        if os.path.exists(f): os.remove(f)
    seed.build(new)  # built beside the live file and swapped in, so readers never see a half-built database
    c = sqlite3.connect(new)
    for r in keep:
        c.execute("INSERT OR REPLACE INTO closes VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(r))
    c.commit(); c.close()
    os.replace(new, DB)
    for f in ("json", "csv"):
        try: os.remove(os.path.join(RESTORED, f"{PERIOD}.{f}"))
        except OSError: pass
    return {"ok": True}


ROUTES = [  # (method, regex, handler, needs_login)
    ("POST", r"/api/login", h_login, False), ("POST", r"/api/logout", h_logout, False), ("GET", r"/api/users", h_users, False),
    ("GET", r"/api/me", h_me, True), ("GET", r"/api/summary", h_summary, True), ("GET", r"/api/txns", h_txns, True),
    ("GET", r"/api/txns/([^/]+)", h_txn, True), ("POST", r"/api/approve", h_approve, True),
    ("POST", r"/api/batch-check", h_batch_check, True), ("POST", r"/api/batch-approve", h_batch_approve, True),
    ("POST", r"/api/route", h_route, True), ("GET", r"/api/inbox", h_inbox, True), ("POST", r"/api/route/(\d+)/reply", h_reply, True),
    ("POST", r"/api/cancel", h_cancel, True), ("POST", r"/api/action", h_action, True), ("POST", r"/api/reopen", h_reopen, True),
    ("POST", r"/api/insights/([^/]+)/regenerate", h_regenerate, True), ("GET", r"/api/deck", h_deck, True),
    ("GET", r"/api/insights", h_insights, True), ("POST", r"/api/commentary", h_commentary, True),
    ("POST", r"/api/enhance", h_enhance, True), ("POST", r"/api/pin", h_pin, True), ("GET", r"/api/audit", h_audit, True),
    ("POST", r"/api/close/finalize", h_finalize, True), ("GET", r"/api/close/status", h_close_status, True),
    ("POST", r"/api/close/verify", h_verify, True), ("POST", r"/api/close/restore", h_restore, True),
    ("POST", r"/api/demo/tamper", h_tamper, True), ("POST", r"/api/demo/complete-review", h_complete_review, True), ("POST", r"/api/reset", h_reset, True),
]


NO_LOCK = {h_login, h_logout, h_verify, h_restore, h_enhance, h_finalize, h_regenerate}  # slow or read-mostly: must not stall everyone else


class H(BaseHTTPRequestHandler):
    timeout = 30  # a client that stalls mid-request cannot pin a thread forever

    def log_message(self, *a):
        pass

    def send(self, status, body, ctype="application/json", cookie=None, clear=False):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"sid={cookie}; HttpOnly; Path=/; SameSite=Lax")
        if clear:
            self.send_header("Set-Cookie", "sid=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0")
        self.end_headers()
        self.wfile.write(data)

    def static(self, path):
        rel = "static/index.html" if path == "/" else path.lstrip("/")
        full = os.path.realpath(os.path.join(ROOT, rel)) if not rel.startswith("restored/") else os.path.realpath(os.path.join(RESTORED, rel[len("restored/"):]))
        ok_dirs = (os.path.join(ROOT, "static") + os.sep, os.path.realpath(RESTORED) + os.sep)
        if not full.startswith(ok_dirs) or not os.path.isfile(full):
            return self.send(404, {"error": "not found"})
        self.send(200, open(full, "rb").read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

    def dispatch(self, method):
        url = urlparse(self.path)
        try:
            ck = {k.strip(): v for k, v in (p.split("=", 1) for p in (self.headers.get("Cookie") or "").split(";") if "=" in p)}
            sid = ck.get("sid")
            if url.path.startswith("/restored/") and sid not in SESSIONS:  # restored closes need a session
                return self.send(401, {"error": "Not signed in"})
            for m, rx, fn, login in ROUTES:
                mm = re.fullmatch(rx, url.path)
                if m == method and mm:
                    break
            else:
                if method == "GET" and not url.path.startswith("/api/"):
                    return self.static(url.path)
                return self.send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise Err(400, "Bad Content-Length")
            if n > MAX_BODY:
                raise Err(413, "Request body too large")
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except (ValueError, RecursionError):  # deeply nested JSON is a bad body, not a server error
                raise Err(400, "Body must be JSON")
            if not isinstance(body, dict):
                raise Err(400, "Body must be a JSON object")
            c = db()
            try:
                u = c.execute("SELECT * FROM users WHERE id=?", (SESSIONS.get(sid),)).fetchone()
                if login and not u:
                    raise Err(401, "Not signed in")
                x = {"c": c, "u": u, "sid": sid, "body": body, "q": {k: v[0] for k, v in parse_qs(url.query).items()}, "m": mm}
                if method == "POST" and fn not in NO_LOCK:
                    with LOCK: res = fn(x)
                else:
                    res = fn(x)
                self.send(200, res, cookie=x.get("cookie"), clear=x.get("clear_cookie", False))
            finally:
                try: c.close()
                except Exception: pass
        except Err as e:
            self.send(e.status, {k: v for k, v in e.body.items() if v is not None})
        except Exception as e:  # never leak a traceback page to the demo
            print("ERROR", method, self.path, repr(e), file=sys.stderr)
            self.send(500, {"error": "Server error: " + type(e).__name__})

    def do_GET(self): self.dispatch("GET")
    def do_POST(self): self.dispatch("POST")


if __name__ == "__main__":
    if "--anchor-prev" in sys.argv:  # one-off before the demo: anchor August so September links to it via prev
        c = db()
        if c.execute("SELECT 1 FROM closes WHERE period='2026-08'").fetchone():
            sys.exit("2026-08 is already anchored")
        anchor(c, "2026-08", "u_controller")
        print("anchored 2026-08:", close_dict(c.execute("SELECT * FROM closes WHERE period='2026-08'").fetchone()))
        sys.exit(0)
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    if not os.path.exists(DB):
        seed.build(DB)
    migrate()
    print(f"Close Commenter on http://127.0.0.1:{port}  (system2={llm.provider()}, jev={jev.available()}, topic={chain_topic()})")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
