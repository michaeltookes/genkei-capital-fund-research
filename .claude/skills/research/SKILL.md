---
name: research
description: Run a disciplined investment-research session against the Genkei data lake. Loads `prompts/research-methodology.md` and the most recent reflections from `docs/research/decisions/`, then walks the structured checklist (frame → macro → fundamentals → flow/positioning → cross-source → counter-thesis → conclusion). Ends by appending a new decision file. Use when the user says "research X", "look into Y", "is Z a buy", invokes `/research`, or asks an investment question that warrants a logged conclusion rather than a one-off answer.
---

# Research session

A disciplined investment-research session over the Genkei data lake, following the methodology in `prompts/research-methodology.md`. Every session ends with a new decision file appended to `docs/research/decisions/` so the work is durable + auditable.

## Pre-flight (run once before answering the question)

1. **Read `prompts/research-methodology.md`** in full. It's the source of truth for what a session looks like; this skill is just the launcher.
2. **Read `docs/research/README.md`** if this is your first research session in the repo — it explains the directory structure + frontmatter contract.
3. **Read the most recent 5–10 decision files** in `docs/research/decisions/` (newest first by filename). Pay attention to the **Outcome** sections of `status: resolved` files — those are calibration data. Note any patterns: are conclusions consistently over-confident? Under-weighting macro? Each new session should be informed by these.
4. **Verify the data lake is healthy** before relying on it: run `genkei watchlist health` and confirm no EMPTY / STALE / MISSING tags on the sources you're about to query. If a source is dark, either skip questions that depend on it OR flag the dependency explicitly in the decision file.

## Run the methodology

Walk the methodology in `prompts/research-methodology.md` section-by-section against the user's question. Don't skip sections silently. Every section either:

- Has output (a paragraph + the `genkei …` commands you ran), OR
- Is explicitly skipped with a one-line reason ("counter-thesis section omitted — the question is descriptive not directional").

Concrete CLI surface you'll use most:

- `genkei macro --series … --since …` (regime context)
- `genkei filings --ticker … --concept …` (equity fundamentals)
- `genkei tvl --chain … --since …` (crypto TVL trajectory)
- `genkei prices --ticker … --since …` (crypto/equity price; note: equity prices not yet ingested)
- `genkei insiders --ticker …` and `genkei insider-clusters` (insider flow)
- `genkei query "<sql>"` (escape hatch for anything the typed surface doesn't cover)

## Land the decision file

After the methodology is complete:

1. Pick a filename: `docs/research/decisions/<YYYY-MM-DD>-<short-topic-slug>.md`. Date is the session date (or the date of the event the decision is *about* if it's an event-driven entry — be consistent within a session).
2. Copy `docs/research/decisions/_template.md` as the starting structure.
3. Fill in the frontmatter (date, asset, sleeve, horizon, confidence, status: pending, trigger_reassessment). All keys are required — `tests/test_research_decisions.py` validates this in CI.
4. Fill in the body — Frame → Macro context → Fundamentals → Flow & positioning → Phase A (case for + against) → Phase B (counter-thesis) → Conclusion. Use the methodology's prompts as section headers.
5. Leave the `## Outcome (filled in by /reflect-decisions)` section in place but unmodified.
6. **Run `python3 -m unittest discover -s tests`** to confirm the frontmatter validator + the rest of the test suite still pass.
7. **Commit** the new file with subject `Research decision: <topic>` and a 1-2 line body explaining the conclusion + horizon. Push.

## Constraints

- **No write actions on the data lake.** All queries go through the CLI which routes through `genkei query`'s read-only path (or the typed subcommands which never write).
- **Never overwrite an existing decision file.** If reconsidering a prior decision, write a NEW file dated today that explicitly references and supersedes the older one (`related: - decision: <slug>` in frontmatter; explain in the Frame section).
- **Don't fabricate signal.** If a query returns empty / NULL / suspect data, say so in the section rather than skipping over it. The audit trail's value is honest record of what was knowable at the time.
- **One decision per session.** If the question splits into sub-questions, log them as separate decision files. Keeps the reflection cycle clean.

## When the user asks a question that doesn't warrant a decision file

Some research questions are descriptive ("what does the data show about X?") rather than directional ("should I buy X?"). Use judgment:

- If the user explicitly says "log this" or "decision on Y" — run the full methodology + write the file.
- If the user asks an open question — work through the methodology informally, *offer* to land a decision file at the end, but don't force it.
- If the question is data-only (no investment implication) — answer it directly with `genkei` commands; the methodology is overkill.

## Skill boundary

This skill kicks off a session and writes a decision file. It does NOT:

- Run the reflection cycle (`/reflect-decisions` does that).
- Execute trades (that's a different system entirely).
- Modify past decision files (`/reflect-decisions` is the only thing that touches resolved entries).
