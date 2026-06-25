# Open research questions

A lightweight, append-only scratch log for threads worth a later look that surface mid-session and would otherwise evaporate. The **agent** (Claude, during a `/research` session or any analysis) appends questions here; **Michael** triages them by flipping status in place.

This is the lightweight cousin of the decision log (`docs/research/decisions/`, see `docs/research/README.md`): no frontmatter validator, no reflection cycle, no audit guarantee. It's a structured scratchpad — the point is zero friction to append. A question graduates out of here when it either becomes a real `/research` decision file or gets answered inline below.

## Entry format

Newest entries on top. One entry per question:

```markdown
### YYYY-MM-DD — <one-line question>
- **status:** open | resolved
- **context:** which session / decision / asset / backlog item surfaced this, and why it matters
- **outcome:** (filled in when resolved — one line on the answer or where it went)
```

**Appending:** add a new `###` block at the top of the log below. **Resolving:** flip `status: open` → `status: resolved` and add an `outcome:` line — don't delete the entry, the trail is the value.

---

## Log

### 2026-06-24 — Is the `yahoo.candles` NOW (ServiceNow) magnitude bug isolated, or does it affect other tickers?
- **status:** open
- **context:** Surfaced by the B-118 reflection dry run and tracked as backlog **B-124**. `yahoo.candles` carries NOW at ~$101–118 across 2026 where the real security trades ~10× higher (~$1,000); the IPO-date row matches exactly, so it's the right instrument at the wrong magnitude. Return-based reflection alpha cancels a constant scaling offset (why B-124 is low priority), but any absolute-price logic (valuation screens, position sizing, alert thresholds) would be misled. Worth a spot-check of ~5–10 watchlist equities' latest `adj_close` vs an external reference to tell "isolated to NOW" from "systematic split-adjustment bug."

### 2026-06-24 — Does CME futures open interest lead spot crypto on institutional rotation?
- **status:** open
- **context:** Surfaced during the ETH/SOL "OG sellers" research sessions and tracked as backlog **B-104** (deferred — the CmeWS endpoint is now TOS-blocked to non-browser requests). The open question is analytical, not just sourcing: *if* a free daily OI feed reappears, does total-institutional-exposure (daily OI trajectory) actually lead spot price turns, and how does it relate to the weekly CFTC COT positioning view (B-031, shipped)? Answering it would tell us how much to invest in reopening B-104 vs. leaning on COT alone.

---

*Seed entries above demonstrate the format. Append new questions at the top of the Log section.*
