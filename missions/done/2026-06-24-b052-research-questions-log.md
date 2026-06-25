# Stand up the "open research questions" log (B-052)

## Context
**Backlog ref:** B-052 (Phase 4 — Agent layer).

During `/research` sessions the agent routinely surfaces threads worth a later look ("does CME OI lead spot?", "is the NOW magnitude bug isolated?") that have no home today — they evaporate when the session ends. B-052 gives them a durable, append-only home the user can triage.

This is **doc-and-convention only** — no new Python, no CLI subcommand. The agent (Claude, in a research session) appends entries by editing the file per a documented format; the user resolves them by editing in place. Keep it dead simple so it survives without tooling. Do NOT build a `genkei research-questions` CLI surface in this mission — if that's ever wanted, file it separately; over-building here defeats the "zero-friction scratch log" point.

## Acceptance criteria
- [x] `docs/research-questions.md` exists with: a short header explaining what the file is and who appends to it, a documented one-entry format, and a worked seed example.
- [x] The entry format captures **date**, **question**, and **originating context** (which session/decision/asset surfaced it), plus a **status** marker (`open` / `resolved`) the user can flip without breaking the format.
- [x] At least 2 real seed questions are logged from existing material — e.g. the CME-OI-vs-spot lead question (B-104 context) and the `yahoo.candles` NOW magnitude audit (B-124) — each as a fully-formed entry, so the format is demonstrated, not just described.
- [x] A one-line pointer to `docs/research-questions.md` is added to the `/research` skill's wind-down or to `prompts/research-methodology.md` so future sessions know the log exists and append to it.
- [x] `python3 -m unittest discover -s tests` still passes (expected: no code touched, so green unchanged).

## Notes
- Mirror the append-only, human-editable ethos of `docs/research/decisions/` — but this is lighter weight (no frontmatter validator, no reflection cycle). It's a scratchpad with structure, not an audit artifact.
- Resolution convention suggestion: flip `status: open` → `status: resolved` and add a one-line outcome, rather than deleting — keeps the trail.
- Cross-reference: B-052 is the lightweight cousin of the decision log; link to `docs/research/README.md` so the two are discoverable together.

## Completed: 2026-06-24
Created `docs/research-questions.md` — append-only, newest-on-top, with a documented `### date — question` / `status` / `context` / `outcome` block format, a header explaining the agent-appends/user-triages contract, and two real seed entries (CME OI-vs-spot lead from B-104, yahoo NOW magnitude from B-124). Added a discoverability pointer in `prompts/research-methodology.md`'s shortcuts section. Doc-and-convention only — no CLI surface built, per the mission's explicit out-of-scope note. Suite green (1921 tests, no code touched).
