# Missions

Long-running task lists Claude grinds through asynchronously — typically overnight or during weekday hours when Michael can't pair live.

Missions are distinct from backlog items. The backlog is *planned* work (sometimes still missing concrete acceptance criteria); missions are *promoted* work, fully spec'd and ready for the agent to execute without questions.

## Layout

- `missions/pending/` — queued. One markdown file per mission.
- `missions/done/` — completed. Same files after work + checklist marked + completion footer.
- `missions/_template.md` — copy-paste starting point.

Both directories are tracked in git for shared visibility and audit trail.

## Mission file format

**Filename**: `YYYY-MM-DD-<slug>.md` — date prefix sorts oldest-first naturally.

**Content**:

```markdown
# <One-line title — what this mission accomplishes>

## Context
<Why this matters. What assumptions to start from. What NOT to do.
Link to backlog items (e.g. B-013) if the mission resolves one.>

## Acceptance criteria
- [ ] <Concrete check>
- [ ] <Concrete check>

## Notes (optional)
<References, prior art, anything the agent should read first.>
```

Keep missions **scoped** — one decision or one feature per file. If a backlog item is too big for one mission, split it.

## Running the queue

### Manually (local Claude Code)

```
/run-missions
```

The agent reads `CLAUDE.md`, switches to a feature branch (e.g. `missions-YYYY-MM-DD`), then loops over `missions/pending/` until empty. One commit + push per completed mission.

### Scheduled (autonomous)

Use the `/schedule` skill to fire `run-missions` on a cron. Example: nightly at 2 AM:

```
/schedule run-missions cron="0 2 * * *"
```

Exact arguments depend on the `/schedule` skill — check its docs for the current invocation shape. The Routine clones the repo, runs `/run-missions`, and commits/pushes per mission.

## Monitoring

- `git log` on the missions branch — one commit per completed mission.
- `ls missions/pending/` — what's still queued.
- `ls missions/done/` — what's been completed.
- Any file in `pending/` with a `## Blocked:` section needs user attention.

## Backlog vs missions

| | Backlog item | Mission |
|---|---|---|
| Stage | Planned, may need triage | Promoted, ready to execute |
| Detail | Paragraph context + bullet acceptance criteria | Full enough to run without questions |
| Lifecycle | Sits in `docs/backlog.md` until promoted | Lives in `pending/` until done, then `done/` forever |
| Owner | Sometimes user (e.g. watchlist definition) | Always agent |

Promote a backlog item to a mission when (a) its acceptance criteria are concrete, (b) no user-only decisions remain, and (c) it's small enough for one async session.

## Blocked missions

A mission is Blocked when it can't complete without something only the user can supply (a decision, a secret, a server spec, etc.) or when an external dependency is broken. The agent:

1. Leaves the file in `missions/pending/`.
2. Adds a `## Blocked: <one-line reason>` section at the top, with details below.
3. Commits the blocker note (subject: `Block mission: <title>`) and moves to the next mission.

Resolve a blocker by editing the mission file (remove the Blocked section, fix what's needed) and re-running the queue. Never delete a blocked mission — fix it or close it explicitly.

## Conventions

- One mission per file.
- Never delete a mission file.
- Never modify `main` from inside a mission — always a feature branch.
- Tests pass before any commit.
- Blocked missions stay in `pending/` with a `## Blocked:` note explaining why.
