# Close Commenter

A month-end close review tool where a person has to read the numbers and write the commentary in their own words. The AI finds what to look at and polishes the writing afterwards. It never approves anything and it never gets to introduce a number.

Built in one day for the AI Engineer New York Hackathon (MLH) by Salvador Aveldaño. All data is synthetic ("Northgate Labs"). Hedera usage is testnet only.

## The idea

Month-end close tools can already classify, match and accrue. The part nobody wants to automate away is the human sign-off: someone who understood what happened and can explain it. Close Commenter is built around that part.

1. **Review.** 409 transactions for September 2026, split into real activity (bank, AP, card, payroll) and accounting-only entries (accruals, reversals, depreciation, reclasses). Each has code-computed flags and a fast AI anomaly score.
2. **Batch approve, with a gate.** One click approves what passes a safety gate (no flags, under the cap, known vendor, inside the vendor's normal range, preparer is not approver, Jev probability under 50%). The server re-runs the gate; the browser cannot override it. Held items come back with reasons.
3. **Route.** Send items to someone else for a second opinion, investigation, clarification or approval, with a note. Every action is in the audit log.
4. **Decide, not only approve.** A reviewer can do three more things, each enforced by the server and recorded with a reason:
   - **Cancel** an accounting-only entry (accrual, reclass, manual JE). Cash transactions cannot be cancelled, and nobody cancels an entry they prepared (same segregation of duties as approving).
   - **Request a refund** on a cash expense that was paid twice or overpaid. The payment is approved as paid, an action item with the amount to recover goes to the AP / Payments desk, and it stays on the open-items list after the close.
   - **Approve with a resolution**: supported, one-time, timing, or prepayment covering N months.

   The ledger export is never edited. Each decision has a *review adjustment* (cancelled entry: minus its amount; refund: minus the expected recovery; prepayment: minus the future-month share), and every insight shows the number as booked and as adjusted. Example in the seed: the $46,250 Nimbus wire is an annual compute prepayment; approved as 12 months, Cloud Hosting reads +49.5% as booked and +3.9% adjusted.
5. **Two ways to review.** The table with its evidence panel, or **Cards**: one held item at a time with its reasons in words; right approves, left rejects (refund request for cash, cancel for accounting-only), up routes, down skips, with reason chips, undo, and a recap. It counts progress, dollars reviewed and held reasons cleared. Nothing rewards speed.
6. **Insights, then your words.** The app proposes a few board-level insights (for example "Office & Admin is up 13.6%, driven by headcount 46 to 52, spend per head flat"). Small outliers such as a $1,950 catering charge are listed as "kept out of the deck". The **Enhance** button stays locked until you have written at least 25 words yourself. When a review decision changes an insight's facts, its AI summary is marked out of date, the code-written text is shown instead, and one click regenerates it (the number guard checks the new text too).
7. **Number guard.** Every figure in AI text is checked against the numbers the code computed and the numbers in your own draft. Anything else is highlighted as unverified.
8. **Deck.** A 16:9 preview of the close deck: cover and sign-off, executive summary, P&L against budget and prior month, operating-expense bridge, one slide per insight, review and controls, integrity. Charts follow IBCS-style conventions (actual solid, prior month grey, budget outlined, variances coloured by business impact, direct labels, units in the title). Unpinned insights say "Commentary pending" and label the AI or template note as preliminary. Export is the browser's print to PDF.
9. **Anchor on Hedera.** Finalizing builds a canonical close package, hashes it, encrypts it (AES-256-GCM) and writes it to a Hedera Consensus Service topic. The hash and manifest are public. Anyone can verify integrity from the public Mirror Node; the key holder can restore the full approved close without QuickBooks or this app.

## Who does what

| Layer | What it does | What it may not do |
|---|---|---|
| Code (`engine.py`) | Every number, every flag, the batch gate, the volume/rate split, the canonical package | Nothing is delegated to a model |
| Jev, System 1 ([TypeSafe](https://typesafe.ai/blog/introducing-system-one-models-and-jev)) | About 150 ms anomaly probability and confidence per transaction | Approve anything; it is one signal next to the rules |
| Claude, System 2 | Writes the what/context/why text from the facts, polishes your draft | Introduce a figure that is not in the facts or your draft (the guard checks) |
| You | Approve, route, write the commentary, finalize | |
| Hedera | Remembers what was signed and when | Say that it was right |

## Measured on the planted data

The seed plants 8 anomalies (duplicate invoice, new vendor wire at 5x history, three payments split under the $5,000 limit, weekend manual JE, round $25,000 wire, look-alike vendor name, unreversed accrual, an outlier bill). On this synthetic set:

- Rules find 8 of 8.
- Jev with a 0.5 probability cut-off flags 5 of 8 (duplicate, look-alike vendor, new vendor, weekend JE, outlier) and 22 of the 399 other transactions (5.5%). It misses the split payments, the round amount and the unreversed accrual, which the rules catch. This is a small synthetic test, not a detection rate.
- 409 transactions in September, 27 flagged by rules. For the controller the gate passes 350 and holds 59 with reasons (18 of September's transactions, the manual JEs and payroll, are controller-prepared, so the segregation-of-duties rule sends them to the CFO).

`python3 check.py` asserts the engine behaviour offline (planted cases found, none of them batch-eligible, number guard catches the invented figure, canonical hash is stable, a tampered transaction changes the hash and is named).

## Run it

Needs Python 3.10+ (standard library only). Node 20+ is needed only for the Hedera part.

```bash
python3 seed.py          # builds close.db from a fixed seed (cached AI scores and narratives are included)
python3 app.py           # http://127.0.0.1:8000
```

That works with no keys: Jev scores and narratives come from `cache/`, the *Recorded example* button works, live Enhance returns your own text unchanged (it needs an Anthropic key), and Finalize answers 502 (it needs Hedera credentials).

Live AI and Hedera need four environment variables (I load them from a secret manager with `op run --env-file=.env.op -- python3 app.py`; copy `.env.op.example` to `.env.op` and point the references at your own vault; `.env.op` is gitignored and holds references, never values):

| Variable | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | Claude narratives and Enhance (`LLM=anthropic`) |
| `TYPESAFE_API_KEY` | Jev screening (`python3 seed.py --ai` refreshes the cache) |
| `HEDERA_ACCOUNT_ID`, `HEDERA_PRIVATE_KEY` | Testnet account that pays for and signs the topic messages |

```bash
npm install                                  # @hiero-ledger/sdk
node chain.mjs init --force                  # creates your own testnet topic and a local .close.key
rm cache/closes.json && python3 seed.py      # drop my August anchor, which lives on my topic
python3 app.py --anchor-prev                 # anchors August on your topic so September links to it
```
The repository ships `chain.json` with my topic id (public) so the August link shows without keys; without `.close.key` you can check hashes on HashScan but not decrypt.

Demo logins are shown on the login screen (`demo-controller`, `demo-cfo`, ...). This is demo auth over HTTP, not real security.

## Demo script (about 4 minutes; short on time: skip step 5's card view and show only the Cloud Hosting slide in step 8)

1. Cockpit: readiness, flagged count, dollars at risk. "Every number here was computed by code."
2. Review: open the duplicate invoice (`2609-0217`). Rules, Jev score with confidence, vendor history.
3. Batch: *Check eligible* shows ready vs held with reasons. *Try to cheat* force-approves the duplicate and the server answers 403.
4. Route an item to the CFO with a note.
5. Decisions: cancel the weekend JE `2609-0261` (accounting-only); request a refund on one of the three Harbor Design payments (`2609-0223`); approve the Nimbus wire `2609-0300` as a 12-month prepayment. Or do the same three in **Cards** with the arrow keys.
6. Insights: Cloud Hosting now shows +49.5% as booked, +3.9% adjusted, and the AI summary is out of date until you regenerate it. The Office & Admin story; the coffee charge sits under "kept out".
7. Write two sentences. Enhance unlocks. *Recorded example* shows a deliberately wrong "35%" highlighted as unverified (labelled as recorded, not live).
8. Deck: flip through the slides (arrow keys); an insight you have not written yet says "Commentary pending".
9. Demo menu, *Demo shortcut* (clearly labelled: approves everything still open and writes an audit entry, so the duplicate invoice ends up approved too; say so: in real life it would get a reversal next month, the shortcut stands in for the rest of the review), pin, **Finalize**. Fingerprint pill linked to August; open HashScan. After the shortcut, the insights whose flagged items it approved (Office & Admin, Cloud Hosting) refresh their AI summary on their own, about 5 seconds.
10. *Simulate QuickBooks edit*, then **Verify**: red, naming `2609-0231`, $4,980.00 became $49,800.00. **Restore from chain**: green, JSON and CSV download.

## What the number guard is, and is not

It extracts every figure from the AI text (dollars, percents, k/M, x, signs, ranges, bare integers) and accepts a figure only if it matches a number the code computed for that insight (dollars to 0.5%, small numbers and percents to 0.05) or appears in your own draft. Dates, periods and ids like `2609-0217` are treated as labels.

Measured limits: on 2,000 random wrong figures, 48 (2.4%) were wrongly accepted, mostly small integers that happen to equal a real fact such as a headcount. It does not know context ("headcount rose 52%" passes because 52 is a real number in that insight), it does not parse number words ("twenty percent", "doubled"), and anything inside your own draft counts as yours. It is a seat belt for invented figures, not a fact checker. Enhanced text that still carries an unverified figure cannot be pinned.

## Close package v1 (the portable format)

One UTF-8 JSON document with sorted keys and compact separators, hashed with SHA-256 over those exact bytes: company, period, source system label, transactions (id, date, vendor, category, account, amount in cents, kind, source, status, preparer, approver, approval note and time, resolution, memo, source reference, flag codes, Jev result), routes, pinned insights with the final human-written commentary, drivers and budgets. No generation timestamp and no audit rows, so the hash is reproducible.

On chain, one close is three kinds of Hedera Consensus Service messages on one topic: a plain `begin` manifest (period, close id, SHA-256, previous close hash, sizes), the gzip + AES-256-GCM payload in chunks, and a plain `commit`. The `prev` field chains one month to the next.

## What Verify covers

Verify rebuilds the package from the live database and compares it with the one on chain: every transaction field in the package (amounts, vendors, dates, status, approver and note, flag codes, Jev result), routes, pinned commentary, drivers and budgets. A changed transaction is named field by field; a change elsewhere is reported by section. Not covered: flag message wording, risk scores, the audit trail, and other months (each month has its own anchor). It also compares our local close record with the chain's `begin` message.

## What the anchor does and does not prove

- It proves the package existed in that exact form at the consensus timestamp, and that the topic's submit key signed it. The `prev` chain stops silent replacement of an earlier month.
- It does not prove the numbers were right. It is not a substitute for the ledger: restore gives back the approved close package, not the accounting system.
- The topic is public. The payload is encrypted with a key that stays on the machine (`.close.key`, gitignored); lose it and you can still verify the hash but not restore the content. The manifest (period, sizes, timestamps) is public.
- Venue networks can block the SDK's gRPC ports. Verify and restore only need HTTPS to the Mirror Node.

## AI and tool disclosure (MLH)

- **Code:** written today with Claude Code (Claude Sonnet 5.5 as the main agent for the first build and Claude Opus 5.5 for the second pass, with Claude Fable 5.1 writing the design specs for the deck and the card review; parallel Claude subagents for the data/rules engine, AI clients, front end and Hedera sidecar, Claude Fable 5.1 for plan and design review, an Opus advisor for plan review). I wrote the product spec (`hackaton_final_idea.md`) at the start of the build day, before any code; the repository had no code before the event.
- **At runtime:** Anthropic API (Claude Sonnet 5.5) for narratives and Enhance; TypeSafe Jev (System One model) for anomaly scores; Hedera Testnet via `@hiero-ledger/sdk`.
- **Data:** fully synthetic and generated by `seed.py`. Nothing from any employer.

## Review log

Two adversarial reviewers attacked the running server and the chain code (injection, role bypass, malformed input, concurrency, number-guard bypasses, package coverage, key handling). Everything they reproduced was fixed or documented above. Known and accepted: demo auth over plain HTTP with in-memory sessions; one global lock for ordinary writes; the *Demo shortcut* approves everything open (labelled, audited); a user's own text counts as verified; `restored/` holds plaintext after a restore.

Second pass (review decisions, cards, deck): two more reviewers attacked the new endpoints and the adjusted numbers. Fixed: a deeply nested JSON body answered 500; a reply could resolve a withdrawn refund request; a reviewer could cancel an entry they prepared; the requester could close their own refund request (now only the Payments desk can). Under 40 random mixes of decisions every total, bridge and hash tied; the defects there were wording in the code-written narrative (a cancelled negative entry read as a reduction) and are fixed. Known and accepted: reopening a decision after the Payments desk resolved its refund leaves that route resolved; "dollars reviewed" in the card view is summed in the browser; Present mode and Safari printing were not exercised by the automated pass.

## Files

`seed.py` data + cache loader · `engine.py` rules, gate, insights, guard, package · `jev.py` System 1 client · `llm.py` System 2 client · `chain.mjs` Hedera publish/fetch · `app.py` server and API · `static/` UI (`app.js` shell, `cards.js` card review, `deck.js` slides) · `check.py` offline check · `API.md` the build contract · `cache/` recorded AI outputs and the anchored August close.

## License

MIT, see [LICENSE](LICENSE).
