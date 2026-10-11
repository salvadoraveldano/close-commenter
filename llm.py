"""System 2: Claude writes words only. Every figure is checked by engine.number_guard; failures fall back to templates."""
import json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "https://api.anthropic.com/v1/messages"
MODELS = [os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "claude-haiku-5-5"]
used = []  # the model that last answered (a list so threads can share it)

NARRATE_SYS = ("You write 3 short finance-commentary fields for a board pack from the FACTS JSON only: "
               "what (what changed), context (how it compares: prior month, budget, trailing average), why (the driver). "
               "One or two plain sentences each. Every figure you write must appear in the facts; money in the facts is in integer cents, "
               "so write whole dollars with commas (cents / 100, for example $41,800), never k or M abbreviations. Write percentages with exactly the decimal shown in the facts (13.6%, not 14%) and never invent or round any other figure. Do not compute new figures (no sums, differences, ratios), and do not write dates or years. "
               "When facts.adjustment_cents is not 0, state the adjusted figure (adjusted_cents, adjusted_pct_vs_prior) and what the review adjustments were, using facts.review_items; when review_open_count is above 0, say how many flagged items are still open. "
               "No invented departments, vendors, causes or numbers. Also choose chart_type: bar or line. Respond only by calling the commentary tool.")
ENHANCE_SYS = ("You improve the clarity and tone of the USER'S finance commentary for a board pack. Keep their claims, meaning and voice: never change what the draft says and never add a new claim or conclusion, even if the FACTS would support one. "
               "Do not add new facts, causes, vendors, departments or figures. The only numbers you may use are those in the draft "
               "or in the FACTS JSON. Return the improved commentary text only, no preamble.")
TOOL = {"name": "commentary", "description": "Board-pack commentary for one insight.",
        "input_schema": {"type": "object", "required": ["what", "context", "why", "chart_type"],
                         "properties": {"what": {"type": "string"}, "context": {"type": "string"}, "why": {"type": "string"},
                                        "chart_type": {"type": "string", "enum": ["bar", "line"]}}}}


def provider():
    p = os.environ.get("LLM") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "off")
    return "anthropic" if p == "anthropic" and os.environ.get("ANTHROPIC_API_KEY") else "off"


def _msg(system, user, tool=None):
    """One Messages API call -> response dict. Raises on failure. Tries the next model on 404 (model not found)."""
    hdr = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
    for m in (used or MODELS):
        body = {"model": m, "max_tokens": 1500, "system": system, "messages": [{"role": "user", "content": user}]}
        if tool:
            body["tools"] = [tool]  # ponytail: no forced tool_choice, claude-sonnet-5-5 rejects it with a 400; the prompt demands the tool call
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(), hdr), timeout=30) as r:
                    out = json.load(r)
                used[:] = [m]
                return out
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break  # try the next model
                if e.code not in (429, 529) or attempt == 2:
                    raise
            except (urllib.error.URLError, OSError):
                if attempt == 2:
                    raise
            time.sleep(1 + attempt)
    raise RuntimeError("no model available")


def _facts(i):
    return json.dumps({k: i.get(k) for k in ("title", "category", "explanation", "facts")}, sort_keys=True)


def _narrate_one(i):
    import engine
    t = {"what": "", "context": "", "why": ""}
    try:
        out = next(b["input"] for b in _msg(NARRATE_SYS, "FACTS:\n" + _facts(i), TOOL)["content"] if b["type"] == "tool_use")
        text = " ".join(str(out[k]) for k in t)
        if not engine.number_guard(text, engine.facts_numbers(i["facts"]))["unverified"] and out["chart_type"] in ("bar", "line"):
            return {**{k: str(out[k]) for k in t}, "chart_type": out["chart_type"], "origin": "ai"}
    except Exception:
        pass
    return {**engine.template_narrative(i), "chart_type": i.get("chart", {}).get("type", "bar"), "origin": "template"}


def narrate(insights):
    """[insight] -> {insight_id: {what, context, why, chart_type, origin: 'ai'|'template'}}."""
    if provider() == "off":
        import engine
        return {i["id"]: {**engine.template_narrative(i), "chart_type": i.get("chart", {}).get("type", "bar"), "origin": "template"}
                for i in insights}
    with ThreadPoolExecutor(8) as ex:
        return {i["id"]: r for i, r in zip(insights, ex.map(_narrate_one, insights))}


def enhance(draft, facts):
    """Polish the user's own words; returns text, or None on any failure (or provider off)."""
    if provider() == "off":
        return None
    try:
        r = _msg(ENHANCE_SYS, f"FACTS:\n{json.dumps(facts, sort_keys=True)}\n\nDRAFT:\n{draft}")
        return "".join(b.get("text", "") for b in r["content"] if b["type"] == "text").strip() or None
    except Exception:
        return None


def _smoke():
    if provider() == "off":
        sys.exit("LLM off or ANTHROPIC_API_KEY not set (run via op run --env-file=.env.op)")
    facts = {"current_cents": 4180000, "prior_cents": 3030000, "budget_cents": 3500000, "avg3_cents": 3100000,
             "pct_vs_prior": 38.0, "pct_vs_budget": 19.4, "driver": "headcount", "driver_current": 52, "driver_prior": 40,
             "driver_pct_change": 30.0, "volume_effect_cents": 909000, "rate_effect_cents": 241000,
             "per_driver_current_cents": 80385, "per_driver_prior_cents": 75750}
    ins = {"id": "ins-smoke", "title": "Office & Admin", "category": "Office & Admin", "facts": facts, "explanation": "mixed",
           "chart": {"type": "bar", "title": "Office & Admin", "labels": ["Prior", "Current", "Budget"],
                     "series": [{"name": "Spend", "values": [3030000, 4180000, 3500000]}]}}
    n = narrate([ins])["ins-smoke"]
    print("narrate: origin=%s chart_type=%s words what/context/why=%d/%d/%d" % (
        n["origin"], n["chart_type"], *(len(n[k].split()) for k in ("what", "context", "why"))))
    draft = ("Office and admin spend rose because we hired more people, so total costs went up in line "
             "with headcount and the per person cost stayed about flat since last month.")
    e = enhance(draft, facts)
    print("enhance: %s draft_words=%d enhanced_words=%s" % ("ok" if e else "None", len(draft.split()), len(e.split()) if e else 0))
    print("model used:", used[0] if used else "none")


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        _smoke()
