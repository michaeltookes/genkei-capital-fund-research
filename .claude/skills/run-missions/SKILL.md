---
name: run-missions
description: Process the mission queue at `missions/pending/` — pick the oldest mission, execute it, move to `missions/done/`, repeat until pending is empty. Use when the user says "run missions", "process the queue", "work the mission queue", invokes `/run-missions`, or fires this via `/schedule`. Designed for autonomous overnight operation.
---

# Run mission queue

Process the project's mission queue: `missions/pending/` → execute → `missions/done/`. One commit + push per completed mission so the user can monitor progress in real-time.

## Pre-flight (run once before the loop)

1. Verify `missions/pending/` exists. If not, tell the user there is no queue here and stop.
2. Read `CLAUDE.md` and `docs/missions.md` to ground in project conventions before processing any mission.
3. Confirm a clean working tree (`git status`). If dirty, stop and tell the user — don't risk mixing mission output with their in-progress work.
4. Note the current branch. If on `main`, switch to a feature branch first (e.g. `missions-YYYY-MM-DD`). Missions never commit to `main`.
5. List `missions/pending/` (excluding `.gitkeep`) sorted alphabetically by filename — this is the run order.

## Loop

For each mission in order, until pending is empty:

1. **Read** the full mission file. Parse the title, context, checklist, and any notes. If it already has a `## Blocked:` section, record it in the summary and move to the next mission without editing or committing it again.
2. **Plan** the work. If the mission is ambiguous, conflicts with project conventions, or its acceptance criteria can't be met without user input — handle as **Blocked** (see below). Don't guess.
3. **Execute**. Use any tools needed. Follow the conventions in `CLAUDE.md` (tests, secrets, branches, PR-body shape, short commit messages).
4. **Validate**. Run the project's test command (`python3 -m unittest discover -s tests` for this repo) before every mission commit, including doc-only and blocker-note commits. If tests fail, do not auto-commit, merge, revert changes, or continue autonomous work. Mark the mission as **Blocked** for test failures, include the failing test output/logs and relevant failing test names in the blocked note, then create a GitHub issue or PR comment with reproduction steps and wait for human intervention.
5. **Mark complete**:
   - Tick every checklist box in the mission file.
   - Append a `## Completed: YYYY-MM-DD` footer with a one-line summary of what was done.
   - Move the file from `missions/pending/` to `missions/done/` (use `git mv` so history is preserved).
6. **Commit** with `Complete mission: <mission title>` as the subject. Body: 1–3 lines on what changed and the mission file path.
7. **Push** after each commit so progress is visible.

## When a mission is Blocked

A mission is Blocked when it can't complete without something only the user can supply (a decision, a secret, a server spec, etc.) or when an external dependency is broken.

- Leave the file in `missions/pending/`.
- Prepend a `## Blocked: <one-line reason>` section after `## Context` and above `## Acceptance criteria`. Detail what's needed below the heading.
- Commit + push the blocker note. Subject: `Block mission: <mission title>`.
- Move on to the next pending mission.

Never delete a mission file. Never silently skip one.

## Stopping

Stop when `missions/pending/` contains zero files (other than `.gitkeep`), or when every remaining file has a `## Blocked:` section.

When stopped, print a summary:

- Missions completed (count + titles).
- Missions blocked (count + titles + one-line reason each).
- Any follow-up the user should review (failed tests, surprising findings, suggested new backlog items).

## What this skill does NOT do

- Open PRs. The user does that via `/pr` after reviewing the missions branch.
- Modify `main` directly. Always work on a feature branch.
- Override blocked missions. The user resolves blockers.
- Touch backlog items not promoted to a mission file.
- Delete or rename missions for any reason.

## Reference

- Mission format: see `missions/_template.md` and `docs/missions.md`.
- Project conventions: `CLAUDE.md`.
