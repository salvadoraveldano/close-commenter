"""System 1: TypeSafe Jev (fast anomaly screen). Never approves anything; never raises."""
import json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "fraud": {"type": "noul",
              "instructions": "Does this journal or payment transaction look like fraud, error or a control breach that a finance reviewer should stop and investigate? Use the vendor history and code flags as evidence.",
              "criteria": {"true": "Likely fraud, duplicate, or control breach", "false": "Routine and consistent with history"}},
    "risk_type": {"type": "choice",
                  "instructions": "Which pattern best describes this transaction?",
                  "criteria": {"duplicate": "Same invoice or payment repeated",
                               "split_payment": "Payments split to stay under an approval limit",
                               "new_vendor": "First-time vendor with unusually large amount",
                               "weekend_unusual": "Manual entry at an unusual time by an unusual user",
                               "round_amount": "Round-number payment without support",
                               "related_party": "Possible related-party or lookalike vendor",
                               "normal": "Routine transaction"}},
    "unusualness": {"type": "score",
                    "instructions": "How unusual is this transaction compared with the vendor and category history?",
                    "criteria": ["Routine", "Unusual", "Highly unusual"]},
}


def available():
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def _call(state):
    """One HTTP call -> (response dict, wall ms of the successful attempt). Raises on failure."""
    body = json.dumps({"state": state, "model": "jev-latest", "questions": QUESTIONS}).encode()
    hdr = {"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"], "Content-Type": "application/json"}
    for attempt in range(3):  # first try + 2 retries
        t0 = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, body, hdr), timeout=10) as r:
                return json.load(r), int((time.time() - t0) * 1000)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 529) or attempt == 2:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == 2:
                raise
        time.sleep(1 + attempt)  # ponytail: fixed 1s/2s backoff, no jitter


def _jev(resp, ms):
    a = resp.get("answers", resp)
    rt, un = a["risk_type"], a["unusualness"]
    return {"fraud": float(a["fraud"]["noul"]),
            "confidence": min(float(rt["confidence"]), float(un["confidence"])),
            "risk_type": rt["choice"], "unusualness": float(un["score"]) / 2,
            "latency_ms": ms, "origin": "live"}


def _one(item):
    try:
        return item[0], _jev(*_call(item[1]))
    except Exception:
        return item[0], None


def screen(states):
    """{txn_id: state} -> {txn_id: jev dict}; failures and a missing key are omitted."""
    if not states or not available():
        return {}
    with ThreadPoolExecutor(8) as ex:
        return {k: v for k, v in ex.map(_one, states.items()) if v}


def _smoke():
    def st(vendor, cat, amt, date, src, memo, seen, med, first, mult, flags):
        return {"txn": {"vendor": vendor, "category": cat, "amount_cents": amt, "date": date, "kind": "activity",
                        "source": src, "memo": memo, "preparer": "u_preparer"},
                "vendor_history": {"periods_seen": seen, "median_cents": med, "first_seen": first},
                "category": {"median_cents": med or 900000, "multiple_vs_median": mult}, "code_flags": flags}
    states = {
        "routine_rent": st("Harbor Properties LLC", "Rent & Occupancy", 1850000, "2026-09-01", "ap",
                           "September office rent", 12, 1850000, "2025-09", 1.0, []),
        "duplicate_invoice": st("Brightline Consulting", "Professional Fees", 1240000, "2026-09-18", "ap",
                                "Invoice INV-4471 (same invoice and amount paid 2026-09-14)", 9, 1240000, "2025-12", 1.0, ["duplicate"]),
        "new_vendor_wire": st("Meridian Advisory Partners", "Professional Fees", 4800000, "2026-09-22", "bank",
                              "Wire: advisory retainer", 0, 0, "2026-09-22", 5.1, ["new_vendor"]),
    }
    if not available():
        sys.exit("TYPESAFE_API_KEY not set (run via op run --env-file=.env.op)")
    model = "?"
    for k, s in states.items():
        try:
            resp, ms = _call(s)
            model = resp.get("model", model)
            j = _jev(resp, ms)
            print(f"{k:18} fraud={j['fraud']:.3f} conf={j['confidence']:.3f} risk_type={j['risk_type']} "
                  f"unusual={j['unusualness']:.2f} latency_ms={j['latency_ms']}")
        except urllib.error.HTTPError as e:
            print(f"{k:18} HTTP {e.code}")
        except Exception as e:
            print(f"{k:18} FAILED {type(e).__name__}")
    print("response model:", model)
    t0 = time.time()
    out = screen(states)
    print(f"screen(): {len(out)}/{len(states)} ok in {int((time.time() - t0) * 1000)} ms wall")


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        _smoke()
