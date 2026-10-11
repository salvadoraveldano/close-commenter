#!/usr/bin/env python3
"""Deterministic synthetic ledger for Northgate Labs (invented company, synthetic data only).
python3 seed.py          builds close.db
python3 seed.py --narratives   refreshes only the insight narratives (no JEV re-screen)
python3 seed.py --ai     also screens the closing period with JEV and narrates insights, rewriting cache/*.json"""
import calendar, copy, json, os, random, sqlite3, sys
from datetime import date, timedelta
import engine

HERE = os.path.dirname(os.path.abspath(__file__))
PERIODS = [f"{2025 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}" for i in range(13)]   # 2025-09 .. 2026-09
HEADS = [40, 40, 41, 41, 42, 43, 43, 44, 45, 45, 46, 46, 52]
OCC = [44, 45, 46, 48, 49, 50, 52, 53, 54, 55, 57, 58, 71]
CUST = [16, 17, 17, 18, 19, 20, 21, 21, 22, 23, 23, 24, 27]
CLOSE = 12
# --- tunables (per-head ticket counts drive txn volume and Office & Admin / T&E totals)
K_TE, K_OFFICE, C_TE = 2.0, 2.0, 95   # tickets per head (T&E also has a fixed base of C_TE tickets)
NOISE_N = 16                 # natural low-risk outlier flags in the closing month
EVENT_IDX = (2, 5, 8, 11)    # quarterly Summit Events invoices
BUD_SEP = {"Subscription Revenue": 1.06, "Services Revenue": 1.0, "Payroll & Benefits": 48 / 46, "Cloud Hosting": 1.03,
           "Software & SaaS": 1.06, "Marketing": 0.97, "Travel & Entertainment": 1.12, "Office & Admin": 1.01,
           "Professional Fees": 1.05, "Rent & Occupancy": 1.0, "Insurance": 1.0, "Depreciation": 1.03, "Contractors": 1.02}

ACCOUNTS = {"Subscription Revenue": "4000 Subscription Revenue", "Services Revenue": "4100 Services Revenue",
            "Payroll & Benefits": "6000 Payroll & Benefits", "Cloud Hosting": "6100 Cloud Hosting",
            "Software & SaaS": "6200 Software & SaaS", "Marketing": "6300 Marketing",
            "Travel & Entertainment": "6400 Travel & Entertainment", "Office & Admin": "6500 Office & Admin",
            "Professional Fees": "6600 Professional Fees", "Rent & Occupancy": "6700 Rent & Occupancy",
            "Insurance": "6800 Insurance", "Depreciation": "6900 Depreciation", "Contractors": "6150 Contractors"}

USERS = [("u_preparer", "Alex Kim", "preparer", "AP / Payments desk: prepares entries and recovers overpayments. Cannot approve."),
         ("u_controller", "Chava", "controller", "Approves and finalizes the close."),
         ("u_cfo", "Dana Whitfield", "cfo", "Second approver; answers routed questions."),
         ("u_auditor", "Omar Reyes", "auditor", "Read-only review of the trail."),
         ("u_owner", "Priya Nair", "owner", "Read-only executive view.")]

CUSTOMERS = ["Halcyon Retail", "Pinecrest Logistics", "Orbit Health", "Tandem Foods", "BlueRidge Energy", "Cobalt Insurance",
             "Lattice Media", "Meridian Freight", "Sable Analytics", "Quill Education", "Harlow Pharma",
             "Evergreen Credit Union", "Redwood Mobility", "Cinder Robotics", "Atlas Outdoor", "Fernway Travel",
             "Juniper Legal", "Kite Telecom", "Opal Hospitality", "Granite Builders", "Willow Biotech", "Tidewater Marine",
             "Vireo Sports", "Copperline Utilities", "Lumos Staffing", "Ashby Furniture", "Dunmore Grocers"]
SERVICES = [("Sterling Public Sector", 14500, 6), ("Marlow Capital", 9200, 7), ("Ironwood Auto", 18400, 8),
            ("Brightpath Schools", 11800, 9)]

# vendor, category, source, dollars, mode (f fixed, g drift +0.8%/mo ending Aug, h per head), jitter, day, tag
FIXED = [
    ("Amazon Web Services", "Cloud Hosting", "ap", 48000, "g", .04, 3, None), ("Google Cloud", "Cloud Hosting", "ap", 14200, "g", .03, 4, None),
    ("Datadog", "Cloud Hosting", "ap", 9300, "g", .03, 5, None), ("Snowflake", "Cloud Hosting", "ap", 10400, "g", .04, 6, None),
    ("MongoDB Atlas", "Cloud Hosting", "ap", 6900, "g", .03, 7, None), ("Cloudflare", "Cloud Hosting", "ap", 3500, "g", .03, 8, None),
    ("Sentry", "Cloud Hosting", "ap", 2100, "f", .02, 9, None),
    ("Slack", "Software & SaaS", "card", 14.5, "h", .01, 2, None), ("GitHub", "Software & SaaS", "card", 25, "h", .01, 2, None),
    ("Notion", "Software & SaaS", "card", 12, "h", .01, 3, None), ("Salesforce", "Software & SaaS", "ap", 9800, "f", .01, 4, None),
    ("HubSpot", "Software & SaaS", "ap", 4980, "f", .005, 5, "hubspot"), ("Okta", "Software & SaaS", "ap", 3150, "f", .01, 6, None),
    ("Atlassian", "Software & SaaS", "card", 2880, "f", .01, 7, None), ("Zendesk", "Software & SaaS", "ap", 3950, "f", .01, 8, None),
    ("Gong", "Software & SaaS", "ap", 6400, "f", .01, 9, None), ("Vanta", "Software & SaaS", "ap", 1950, "f", .01, 10, None),
    ("Acme Cloud Services", "Software & SaaS", "ap", 5800, "f", .02, 11, "acme"), ("Zoom", "Software & SaaS", "card", 1260, "f", .01, 12, None),
    ("Google Ads", "Marketing", "card", 26000, "f", .04, 3, None), ("LinkedIn Ads", "Marketing", "card", 15200, "f", .04, 4, None),
    ("Orion Marketing Agency", "Marketing", "ap", 18000, "f", .005, 5, None), ("Lumen Content Studio", "Marketing", "ap", 9200, "f", .06, 12, None),
    ("Canva", "Marketing", "card", 840, "f", .05, 14, None), ("Mailchimp", "Marketing", "card", 620, "f", .05, 15, None),
    ("Flexspace Overflow Seats", "Office & Admin", "ap", 480, "h", .01, 1, None), ("Fresh Pantry Snacks", "Office & Admin", "ap", 240, "h", .01, 10, None),
    ("BrightClean Janitorial", "Office & Admin", "ap", 185, "h", .01, 3, None), ("Frontier IT Support", "Office & Admin", "ap", 260, "h", .01, 4, None),
    ("Gridline Utilities", "Office & Admin", "ap", 5400, "f", .05, 15, None), ("Frontier Internet", "Office & Admin", "ap", 3100, "f", .01, 20, None),
    ("Mailbox Courier", "Office & Admin", "ap", 1200, "f", .08, 22, None), ("SecureBadge Access", "Office & Admin", "ap", 2200, "f", .02, 18, None),
    ("Calloway Tax Advisors", "Professional Fees", "ap", 3200, "f", .03, 9, "calloway"), ("Hartwell & Boyd LLP", "Professional Fees", "ap", 6200, "f", .12, 12, None),
    ("Ellis Compliance Consulting", "Professional Fees", "ap", 1500, "f", .10, 17, "ellis"), ("PeopleFirst Recruiting", "Professional Fees", "ap", 5500, "f", .10, 20, None),
    ("Pierpont Realty Trust", "Rent & Occupancy", "ap", 52400, "f", 0, 1, None), ("Pierpont CAM Charges", "Rent & Occupancy", "ap", 3850, "f", .02, 13, "cam"),
    ("Hartford Property & Casualty", "Insurance", "ap", 4200, "f", 0, 6, None), ("Chubb D&O", "Insurance", "ap", 2950, "f", 0, 6, None),
    ("Embroker Cyber", "Insurance", "ap", 1820, "f", 0, 6, None),
    ("Kestrel Engineering Group", "Contractors", "ap", 24000, "f", .04, 19, "kestrel"), ("Harbor Design Co", "Contractors", "ap", 4700, "f", .05, 16, "harbor"),
    ("Brightline Staffing", "Contractors", "ap", 16800, "f", .05, 21, None), ("Vale Data Labeling", "Contractors", "ap", 6100, "f", .08, 23, None),
    ("Ortega UX Research", "Contractors", "ap", 3400, "f", .10, 24, None), ("Fathom Security Testing", "Contractors", "ap", 5200, "f", .05, 25, None),
]
# vendor, category, dollars per ticket, weight (weights sum to 1 within a category)
CARDS = [
    ("Delta Air Lines", "Travel & Entertainment", 520, .13), ("United Airlines", "Travel & Entertainment", 480, .11),
    ("Marriott", "Travel & Entertainment", 340, .12), ("Hilton", "Travel & Entertainment", 295, .09),
    ("Uber", "Travel & Entertainment", 36, .22), ("Lyft", "Travel & Entertainment", 31, .09),
    ("Capital Grille", "Travel & Entertainment", 640, .06), ("Sweetgreen", "Travel & Entertainment", 78, .10),
    ("Amtrak", "Travel & Entertainment", 190, .08),
    ("Staples", "Office & Admin", 420, .17), ("Amazon Business", "Office & Admin", 880, .27), ("CDW", "Office & Admin", 2100, .13),
    ("Apple Business", "Office & Admin", 2450, .11), ("IKEA Business", "Office & Admin", 1350, .10),
    ("Bluebird Coffee & Catering", "Office & Admin", 420, .22),
]
PAYROLL = [("Payroll - Engineering", .38), ("Payroll - Product", .12), ("Payroll - Sales", .18),
           ("Payroll - Customer Success", .14), ("Payroll - G&A", .10), ("Payroll - Marketing", .08)]
PER_HEAD = [("Payroll Taxes - ADP", 1050), ("Health Benefits - Anthem", 890), ("401k Match - Guideline", 520)]
ACCRUALS = [("Brightline Staffing", "Contractors", 16800), ("Hartwell & Boyd LLP", "Professional Fees", 5800),
            ("Calloway Tax Advisors", "Professional Fees", 3200)]

SCHEMA = """
CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, role TEXT, password TEXT, hint TEXT);
CREATE TABLE txns(id TEXT PRIMARY KEY, date TEXT, period TEXT, vendor TEXT, category TEXT, account TEXT, amount_cents INTEGER,
  kind TEXT, source TEXT, preparer TEXT, approver TEXT, approve_note TEXT, approve_ts TEXT, memo TEXT,
  source_ref TEXT, status TEXT DEFAULT 'pending', flags TEXT DEFAULT '[]', risk INTEGER DEFAULT 0, jev TEXT, resolution TEXT);
CREATE TABLE drivers(period TEXT PRIMARY KEY, headcount INTEGER, occupancy_pct REAL, customers INTEGER);
CREATE TABLE budget(period TEXT, category TEXT, amount_cents INTEGER, PRIMARY KEY(period, category));
CREATE TABLE routes(id INTEGER PRIMARY KEY AUTOINCREMENT, txn_ids TEXT, from_user TEXT, to_user TEXT, reason TEXT, note TEXT,
  status TEXT DEFAULT 'open', reply TEXT, ts TEXT, reply_ts TEXT);
CREATE TABLE commentary(insight_id TEXT PRIMARY KEY, user_id TEXT, draft TEXT, enhanced TEXT, final TEXT, pinned INTEGER DEFAULT 0, ts TEXT, facts_hash TEXT);
CREATE TABLE audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user_id TEXT, action TEXT, target TEXT, detail TEXT);
CREATE TABLE closes(close_id TEXT PRIMARY KEY, period TEXT, hash TEXT, prev_hash TEXT, topic_id TEXT, messages INTEGER, bytes INTEGER,
  tier TEXT, links TEXT, closed_by TEXT, ts TEXT);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""
TABLES = ["users", "txns", "drivers", "budget", "routes", "commentary", "audit", "closes", "meta"]


def jit(r, cents, pct):
    return int(round(cents * (1 + r.uniform(-pct, pct))))


def wd(y, m, d):
    """weekday on or after day d, staying inside the month"""
    dt = date(y, m, min(d, calendar.monthrange(y, m)[1]))
    while dt.weekday() > 4:
        dt += timedelta(days=1)
    if dt.month != m:
        dt = date(y, m, calendar.monthrange(y, m)[1])
        while dt.weekday() > 4:
            dt -= timedelta(days=1)
    return dt


def gen(r, i, custa, acc_ref):
    """all transactions of period index i (unsorted, no ids yet)"""
    p = PERIODS[i]
    y, m = int(p[:4]), int(p[5:])
    heads, out = HEADS[i], []
    last = wd(y, m, 31)
    first = wd(y, m, 1)

    def add(vendor, cat, src, cents, day=None, memo="", kind="activity", tag=None, prep=None, d=None):
        out.append({"vendor": vendor, "category": cat, "source": src, "amount_cents": cents, "kind": kind, "memo": memo, "tag": tag,
                    "date": d or wd(y, m, day), "period": p,
                    "preparer": prep or ("u_controller" if kind == "accounting" or src == "payroll"
                                         else "u_controller" if r.random() < .08 else "u_preparer")})
    for k in range(CUST[i]):
        add(CUSTOMERS[k], "Subscription Revenue", "bank", jit(r, custa[k] * 100, .015), 1 + k % 5, f"Subscription invoice {p}")
    for name, base, day in SERVICES:
        add(name, "Services Revenue", "bank", jit(r, base * 100, .03), day, f"Professional services invoice {p}")
    for name, share in PAYROLL:
        add(name, "Payroll & Benefits", "payroll", jit(r, int(heads * share * 11200 * 100), .012), 15, f"Payroll run {p}")
    for name, per in PER_HEAD:
        add(name, "Payroll & Benefits", "payroll", jit(r, heads * per * 100, .012), 16, f"{name.split(' - ')[0]} {p}")
    for v, cat, src, dol, mode, j, day, tag in FIXED:
        scale = {"f": 1, "g": 1 - .008 * (11 - i), "h": heads}[mode]
        add(v, cat, src, jit(r, dol * 100 * scale, j), day, f"{v} {p}", tag=tag)
    if i in EVENT_IDX:
        add("Summit Events Co", "Marketing", "ap", jit(r, 3400000, .05), 14, f"Quarterly event sponsorship {p}")
    for cat, K, C in (("Travel & Entertainment", K_TE, C_TE), ("Office & Admin", K_OFFICE, 0)):
        for v, c, dol, w in CARDS:
            if c != cat:
                continue
            for _ in range(max(1, int(round(w * (K * heads + C) + r.uniform(-.6, .6))))):
                add(v, cat, "card", jit(r, dol * 100, .12), r.randint(1, 31),
                    f"{v} card purchase", tag="coffee" if v.startswith("Bluebird") else None)
    # accounting-only entries
    for v, cat, dol in ACCRUALS:
        add(v, cat, "accrual", jit(r, dol * 100, .03), 31, f"Accrue unbilled {v} services (reverses next month)", "accounting", d=last)
        prev = acc_ref.get((v, PERIODS[i - 1])) if i else None
        if prev:
            add(v, cat, "reversal", -prev[1], 1, f"Auto-reversal of {prev[0]}", "accounting", tag="rev", d=first)
    add("Depreciation - Equipment", "Depreciation", "depr", jit(r, int(1460000 * (1 + .01 * i)), .002), 31, f"Monthly depreciation {p}", "accounting", d=last)
    add("Amortization - Capitalized Software", "Depreciation", "depr", jit(r, 830000, .002), 31, f"Monthly amortization {p}", "accounting", d=last)
    add("GL Reclass - Prepaids", "Software & SaaS", "reclass", jit(r, 260000, .10), 31, f"Reclass prepaid software {p}", "accounting", d=last)
    add("FX Revaluation - EUR payables", "Software & SaaS", "je", jit(r, 120000, .15), 31, f"Month-end FX revaluation {p}", "accounting", d=last)
    return out


def build(path="close.db"):
    r = random.Random(42)
    custa = [r.randint(11000, 21000) if k < 20 else r.randint(15500, 16500) for k in range(len(CUSTOMERS))]
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for t in TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?)", [(u, n, ro, f"demo-{ro}", h) for u, n, ro, h in USERS])
    conn.executemany("INSERT INTO drivers VALUES (?,?,?,?)", [(PERIODS[i], HEADS[i], OCC[i], CUST[i]) for i in range(13)])
    acc_ref, refn, all_txns = {}, {}, []
    for i, p in enumerate(PERIODS):
        items = gen(r, i, custa, acc_ref)
        if i == CLOSE:
            items = closing_month(r, items)
        for t in items:
            t["_k"] = r.random()
        items.sort(key=lambda t: (t["date"], t["_k"]))
        if i == CLOSE:
            items = place_special(r, items)
        for n, t in enumerate(items):
            t["id"] = f"{p[2:4]}{p[5:7]}-{n + 1:04d}"
            t["date"] = t["date"].isoformat()
        if i == CLOSE:                                                    # swap so the planted ids land on the intended txns
            for tag, want in (("dup2", "2609-0217"), ("hubspot", "2609-0231")):
                by = {t["id"]: t for t in items}
                a = next(t for t in items if t["tag"] == tag)
                b = by[want]
                a["id"], b["id"] = want, a["id"]
        items.sort(key=lambda t: t["id"])
        for t in items:
            if t["kind"] == "accounting":
                kind = "JE"
            else:
                kind = {"ap": "Bill", "card": "Purchase", "payroll": "Payroll"}.get(t["source"]) \
                    or ("Invoice" if t["category"].endswith("Revenue") else "Payment")
            refn[kind] = refn.get(kind, {"Bill": 10200, "Invoice": 2100, "JE": 700, "Purchase": 5100, "Payment": 8300,
                                         "Payroll": 400}[kind]) + r.randint(1, 3)
            t["source_ref"] = f"qbo:{kind}/{refn[kind]}"
            t["account"] = ACCOUNTS[t["category"]]
            if t["source"] == "accrual":
                acc_ref[(t["vendor"], p)] = (t["source_ref"], t["amount_cents"])
            if i < CLOSE:
                t["approver"] = "u_cfo" if t["preparer"] == "u_controller" else r.choice(["u_controller", "u_cfo"])
                t["approve_note"] = r.choice(["", "", "Matched to invoice", "Reviewed"])
                dd = date.fromisoformat(t["date"]) + timedelta(days=r.randint(1, 3))
                t["approve_ts"] = f"{dd.isoformat()}T14:{r.randint(0, 59):02d}:00Z"
                t["status"] = "approved"
            else:
                t["approver"] = t["approve_note"] = t["approve_ts"] = None
                t["status"] = "pending"
        all_txns += items
    # demo: day-to-day activity in the closing month is prepared by Alex; the controller prepares JEs and payroll (CFO approves those).
    # Done after generation so the seeded random stream, and therefore every number, stays as tuned.
    for t in all_txns:
        if t["period"] == "2026-09" and t["kind"] == "activity" and t["source"] != "payroll":
            t["preparer"] = "u_preparer"
    conn.executemany("INSERT INTO txns(id,date,period,vendor,category,account,amount_cents,kind,source,preparer,approver,approve_note,"
                     "approve_ts,memo,source_ref,status) VALUES (:id,:date,:period,:vendor,:category,:account,:amount_cents,:kind,"
                     ":source,:preparer,:approver,:approve_note,:approve_ts,:memo,:source_ref,:status)", all_txns)
    # budgets: history = actual +/- noise, closing month = prior actual x planned factor
    tot = {}
    for t in all_txns:
        tot.setdefault(t["category"], {})[t["period"]] = tot.setdefault(t["category"], {}).get(t["period"], 0) + t["amount_cents"]
    for cat in engine.CATEGORIES:
        for i, p in enumerate(PERIODS):
            b = int(tot[cat][PERIODS[11]] * BUD_SEP[cat]) if i == CLOSE else int(tot[cat][p] * r.uniform(.96, 1.05))
            conn.execute("INSERT INTO budget VALUES (?,?,?)", (p, cat, b))
    pl = {}
    for t in all_txns:
        if t["period"] == PERIODS[CLOSE] and t.get("_plant"):
            pl.setdefault(t["_plant"], []).append(t["id"])
    pl = {k: sorted(v) for k, v in sorted(pl.items())}
    meta = {"company": engine.COMPANY, "period": engine.PERIOD, "planted": json.dumps(pl, sort_keys=True), "seed": "42",
            "tamper_id": "2609-0231", "coffee_id": next(t["id"] for t in all_txns if t.get("_coffee")), "synthetic": "true"}
    for name in ("jev", "insights", "enhance_recorded"):
        f = os.path.join(HERE, "cache", f"{name}.json")
        if os.path.exists(f):
            txt = open(f).read()
            meta[f"cache_{name}"] = txt
            if name == "jev":
                for tid, j in json.loads(txt).items():
                    conn.execute("UPDATE txns SET jev=? WHERE id=?", (json.dumps(j, sort_keys=True), tid))
    conn.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
    f = os.path.join(HERE, "cache", "closes.json")      # earlier closes anchored on Hedera (public facts): a fresh clone still links Sep to Aug
    if os.path.exists(f):
        for r in json.load(open(f)):
            conn.execute("INSERT OR REPLACE INTO closes VALUES(:close_id,:period,:hash,:prev_hash,:topic_id,:messages,:bytes,:tier,:links,:closed_by,:ts)", r)
    conn.commit()
    engine.flag_period(conn, engine.PERIOD)
    conn.close()


def closing_month(r, items):
    """swap normal September bills for the planted cases and add noise, new vendors"""
    drop = {"kestrel", "harbor", "ellis"}
    items = [t for t in items if not (t["tag"] in drop or (t["tag"] == "rev" and t["vendor"] == "Calloway Tax Advisors"))]
    hist_ellis = 180000
    y, m = 2026, 9

    def mk(vendor, cat, src, cents, d, memo, plant, kind="activity", prep=None, tag=None):
        return {"vendor": vendor, "category": cat, "source": src, "amount_cents": cents, "kind": kind, "memo": memo, "tag": tag,
                "date": d, "period": "2026-09", "preparer": prep or "u_preparer", "_plant": plant}
    items.append(mk("Kestrel Engineering Group", "Contractors", "bank", 2500000, wd(y, m, 19), "", "round_amount"))
    for _ in range(3):
        items.append(mk("Harbor Design Co", "Contractors", "ap", 490000, wd(y, m, 16), "Design sprint invoice", "split_payment"))
    items.append(mk("Ellis Compliance Consulting", "Professional Fees", "ap", 903500, wd(y, m, 17), "Special compliance review", "outlier"))
    items.append(mk("Nimbus Compute LLC", "Cloud Hosting", "bank", 4625000, wd(y, m, 22), "Wire: annual compute prepayment", "new_vendor"))
    items.append(mk("Acme Cloud Svcs", "Software & SaaS", "ap", 589900, wd(y, m, 24), "Acme Cloud Svcs monthly invoice", "near_dup_vendor"))
    items.append(mk("Manual JE (QBO)", "Insurance", "je", 1184000, date(2026, 9, 20), "Prepaid insurance true-up", "weekend_je",
                    kind="accounting", prep="u_preparer"))
    items.append(mk("Pixelwave Swag Co", "Marketing", "card", 124000, wd(y, m, 8), "Branded swag for the event", None))
    items.append(mk("Brightwave Printing", "Marketing", "card", 78000, wd(y, m, 11), "Event collateral printing", None))
    for t in items:
        if t["tag"] == "calloway":
            t["_plant"] = "accrual_no_reversal"
    # coffee outlier: one Bluebird ticket becomes $1,950
    cof = sorted((t for t in items if t["tag"] == "coffee"), key=lambda t: (t["date"], t["amount_cents"]))
    cof[len(cof) // 2].update(amount_cents=195000, _coffee=True, memo="Quarterly all-hands catering")
    # natural noise: a few mildly high card tickets (never planted, coffee, or HubSpot)
    pool = sorted((t for t in items if t["source"] == "card" and t["category"] == "Travel & Entertainment"
                   and not t.get("_plant") and not t.get("_coffee") and t["tag"] is None and 10000 < t["amount_cents"] < 60000),
                  key=lambda t: (t["date"], t["vendor"], t["amount_cents"]))
    for t in r.sample(pool, NOISE_N):
        t["amount_cents"] = int(t["amount_cents"] * r.uniform(1.9, 2.15))
        t["memo"] += " (exceeds usual)"
    return items


def place_special(r, items):
    """duplicate pair (second copy lands on 2609-0217) and the tamper target HubSpot (2609-0231)"""
    cam = next(t for t in items if t["tag"] == "cam")
    d2 = items[216]["date"]
    cam["date"] = d2 - timedelta(days=3)
    dup = copy.deepcopy(cam)
    dup.update(date=d2, tag="dup2", _plant="duplicate", _k=r.random(), memo=cam["memo"] + " (re-submitted)")
    items.append(dup)
    hub = next(t for t in items if t["tag"] == "hubspot")
    hub["amount_cents"] = 498000
    hub["date"] = items[230]["date"]
    items.sort(key=lambda t: (t["date"], t["_k"]))
    return items


def ai_refresh(path, screen=True):
    import jev, llm                                       # only needed for --ai / --narratives
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ids = [x[0] for x in conn.execute("SELECT id FROM txns WHERE period=? ORDER BY id", (engine.PERIOD,))]
    res = jev.screen({i: engine.jev_state(conn, i) for i in ids}) if screen and jev.available() else {}
    ins = engine.insights(conn, engine.PERIOD)["insights"]
    fh = {i["id"]: engine.facts_hash(i) for i in ins}     # a narrative is only served as AI while the facts still hash to this
    nar = llm.narrate(ins)
    conn.close()
    os.makedirs(os.path.join(HERE, "cache"), exist_ok=True)
    if res:
        json.dump(res, open(os.path.join(HERE, "cache", "jev.json"), "w"), indent=1, sort_keys=True)
    ai = {k: {"narrative": {x: v.get(x, "") for x in ("what", "context", "why")}, "chart": {"type": v.get("chart_type")},
              "facts_hash": fh[k]}
          for k, v in nar.items() if v.get("origin") == "ai"}          # never cache template text as AI narrative
    if ai:
        json.dump(ai, open(os.path.join(HERE, "cache", "insights.json"), "w"), indent=1, sort_keys=True)
    build(path)


if __name__ == "__main__":
    out = os.path.join(HERE, "close.db")
    build(out)
    if "--ai" in sys.argv or "--narratives" in sys.argv:
        ai_refresh(out, screen="--ai" in sys.argv)
    print("built", out)
