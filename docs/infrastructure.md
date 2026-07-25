# Infrastructure

How this repo connects to the homelab Postgres that backs the data lake.

> **Source of truth:** the user's `/server-info` skill (local-only, never committed). Detailed credentials and absolute IPs live there. This doc captures only what's safe to share inside the repo, using placeholders.

## Postgres

**Container:** `genkeicapital-postgres`
**Image:** `timescale/timescaledb:2.26.4-pg16`
**Host (LAN):** `<beelink-host>`
**Port:** `5440`
**Docker network:** `mission_control_net`
**Compose path on the server:** `apps/mission-control/genkei-capital/postgres/`

### Connection string shape

```text
postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>
```

Set as `GENKEI_DATABASE_URL` in your local `.env` (gitignored) and as a GitHub Actions secret of the same name. The driver prefix is stripped automatically by `genkei.common.db._resolve_url` when libpq needs a plain URL.

Credentials live in `.env` files on the homelab server (never in this repo). Pull them from the server when wiring up a new dev machine; do not paste them anywhere committed.

## TimescaleDB status

**Decision (2026-05-09):** the homelab container runs `timescale/timescaledb:2.26.4-pg16`. The repo migration that activates the extension is already committed (`migrations/versions/20260509_install_timescaledb_extension.py`).

### Container swap (manual step on the homelab)

```bash
ssh <beelink-host>
cd ~/homelab/apps/mission-control/genkei-capital/postgres/

# Edit docker-compose.yml: replace `image: postgres:16-alpine` with
# `image: timescale/timescaledb:2.26.4-pg16`. Same data dir, same port,
# same network — drop-in replacement.

docker compose down
docker compose up -d
docker compose logs -f genkeicapital-postgres   # confirm it's healthy
```

Then, from a developer machine pointed at the new container:

```bash
.venv/bin/alembic upgrade head
```

The activation migration creates `timescaledb` only when the extension is not already present. If the swap didn't happen, that statement fails loudly — by design, no silent degradation.

### Fallback

If the swap turns out to be impractical (license concerns, homelab compatibility issue), back out by reverting the migration's effect (drop the extension on the database) and switch to `pg_partman` + hand-written rollups per the alternative path in `docs/storage.md`. Documented but not pursued unless TimescaleDB itself becomes a problem.

### Hypertable activation

Per `docs/storage.md`, hypertables live in their own migrations, separate from the base table DDL. Each per-source schema migration creates plain tables; a follow-up migration in the same series promotes the time-series tables to hypertables via `SELECT create_hypertable(...)`. This keeps the tables usable on plain PG even before TimescaleDB activates.

## Network reachability

The Beelink is on a private LAN behind a double-NAT path (Google Nest Wifi → OPNsense → ISP). **GitHub-hosted Action runners cannot reach the homelab from the internet.**

**Decision (2026-05-09):** the daily DeFiLlama pipeline runs on a self-hosted runner installed on the Beelink (B-077). The runner attaches to `mission_control_net`, so it reaches `genkeicapital-postgres` directly by container name with zero public exposure. Cloudflare TCP tunnel was considered as an alternative — building blocks are present (`cloudflared` already runs on the Beelink) but the tunnel adds blast-radius and isn't needed unless cloud runners come back into scope.

For developer machines on the same LAN, direct connection to `<beelink-host>:5440` works once the password is in `.env`.

## Self-hosted GitHub Actions runner

The runner that hosts the scheduled DeFiLlama pipeline lives on the Beelink and is managed via Docker Compose alongside the Postgres container.

### One-time install

1. **Create a fine-grained PAT.** GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens. Scope it to *this single repo* with these permissions:
   - **Administration:** Read and write (so the runner image can mint short-lived registration tokens)
   - **Metadata:** Read (default)

   Set an explicit expiration (90 days is reasonable). Save the token — you will not see it again.

2. **Drop the compose template onto the Beelink.**

   ```bash
   ssh <beelink-host>
   mkdir -p ~/homelab/apps/mission-control/genkei-capital/genkei-runner/
   cd ~/homelab/apps/mission-control/genkei-capital/genkei-runner/

   # From this repo:
   #   infra/runner/docker-compose.yml -> docker-compose.yml
   #   infra/runner/.env.example       -> .env  (then fill in ACCESS_TOKEN)
   ```

3. **Start the runner.**

   ```bash
   docker compose up -d
   docker compose logs -f genkei-runner   # wait for "Listening for Jobs"
   ```

4. **Verify in GitHub.** Repo → Settings → Actions → Runners. Look for `beelink-genkei-1` with status `Idle`. Labels should read `self-hosted, beelink, linux, x64`.

5. **Smoke test.** Trigger the workflow manually: Actions → DeFiLlama Daily Brief → Run workflow. Job should pick up within a few seconds, run `alembic upgrade head`, then collect + normalize against the homelab Postgres.

### Restart procedure

Bring the runner down and back up cleanly without losing state:

```bash
ssh <beelink-host>
cd ~/homelab/apps/mission-control/genkei-capital/genkei-runner/
docker compose restart genkei-runner
docker compose logs -f genkei-runner
```

The `genkei_runner_data` volume preserves the work directory and tool cache, so `actions/setup-python@v5` doesn't re-download Python on every restart.

### "Jobs queue forever" diagnostic

If a workflow run stays in `Queued` for more than ~30 seconds, walk this list in order:

1. **Runner reachable?** `docker ps --filter name=genkei-runner` on the Beelink. If the container is gone or restarting, check `docker compose logs genkei-runner` for the failure reason.
2. **Runner online in GitHub?** Repo → Settings → Actions → Runners. Status should be `Idle`. If it's `Offline`, the runner has lost connectivity (DNS, outbound HTTPS to api.github.com).
3. **Labels match?** The workflow uses `runs-on: [self-hosted, beelink]`. The runner advertises `self-hosted, beelink, linux, x64`. If you renamed the runner without updating its labels, jobs go unmatched.
4. **PAT expired?** When the registration token call fails, the container logs an HTTP 401 on startup. Rotate the PAT (next section), update `.env`, `docker compose up -d --force-recreate`.
5. **Egress blocked?** OPNsense or upstream firewall blocking `https://api.github.com`. Test from inside the container: `docker compose exec genkei-runner curl -I https://api.github.com`.

### PAT rotation

Tokens have an explicit expiry. To rotate without downtime:

```bash
# 1. Mint a new PAT in GitHub (same scopes as install).
# 2. On the Beelink:
ssh <beelink-host>
cd ~/homelab/apps/mission-control/genkei-capital/genkei-runner/
$EDITOR .env                                  # paste the new ACCESS_TOKEN
docker compose up -d --force-recreate         # picks up the new env
docker compose logs -f genkei-runner          # confirm "Listening for Jobs"
# 3. Revoke the old PAT in GitHub Settings → Developer settings.
```

If the old PAT expires before rotation completes, the runner stays registered (the registration token it last minted is good for ~1 hour) but cannot mint a new one — restart it after the new PAT is in place.

### Removing the runner

If the runner is being decommissioned:

```bash
ssh <beelink-host>
cd ~/homelab/apps/mission-control/genkei-capital/genkei-runner/
docker compose down
docker volume rm genkei_runner_data   # only if you're sure
```

GitHub will mark the runner `Offline` after a few minutes; you can delete it from Settings → Actions → Runners once it's no longer needed.

## Monitoring & alerting

The lake is fed by 14 scheduled ingest workflows. Three workflows watch for it going stale, layered so each covers a window the others can't (B-071, B-119):

| Workflow | Runs on | Catches | Channel |
|---|---|---|---|
| `workflow-failure-alert.yml` | GitHub-hosted (`workflow_run`) | A watched workflow that *ran and failed/timed out* | GitHub issue + Discord |
| `ingest-staleness-check.yml` | **Beelink** (needs Postgres) | A source that ran but wrote no/stale rows — DB-level freshness via `genkei watchlist health` | GitHub issue (per source) + Discord summary |
| `ingest-heartbeat.yml` | **GitHub-hosted** (Actions API) | A workflow with no *successful run* in its cadence + grace — i.e. the Beelink runner itself being down, which the other two can't see | GitHub issue + Discord |

**Why the heartbeat is GitHub-hosted:** a down self-hosted runner never *starts* its scheduled jobs, so nothing fails (no failure alert) and the DB-side staleness check can't run either (it lives on the same runner). The heartbeat sidesteps both by living on GitHub-hosted compute and reading only the Actions API — it stays up when the homelab is down. It uses run *recency* (last successful run age) rather than DB freshness because GitHub-hosted runners can't reach the homelab Postgres (see "Network reachability").

### Retry on transient failure (B-125)

Monitoring above is the *observability* half of silent-staleness — you find out when the lake stops being fed. Retry is the *prevention* half: each ingest workflow ran its collector exactly once, so a single transient API flake (a slow FRED at 11:00 UTC, a 502 from DeFiLlama) dropped a full day of data until the next cron.

The external-API **collect** step of every ingest workflow is now wrapped in `scripts/ci/retry.sh` — a plain bash retry-with-backoff (no third-party Action, matching the SHA-pinned-action posture):

```
bash scripts/ci/retry.sh <max_attempts> <base_delay_seconds> -- <command...>
```

Contract:

- **Bounded:** 3 attempts, exponential backoff (`base_delay * 2^(n-1)` → 10s, 20s). Worst-case ~30s added before a step fails for real — well inside every workflow's timeout budget.
- **Idempotency-safe:** collectors upsert and wrap each run in a single `meta.ingest_runs` row, so a retried attempt opens a fresh run rather than corrupting accounting. A failed attempt's run is marked `failed`; the successful attempt's run is the one normalize consumes.
- **Run-id preserved:** the command's stdout streams through unchanged. A failed attempt prints no `ingest_run_id=` line (collectors print it only on success), so the workflows that parse `grep ingest_run_id= | tail -n1` still pick the successful attempt's id. Retry diagnostics go to stderr.
- **Scope — deliberately *not* everything:**
  - **Normalize / emit steps are not wrapped** — they read the local Postgres, not a flaky external API.
  - **`workflow_dispatch` backfill / `--since` replay paths run once, never retried** — re-walking a backfill is double-work. The args-array workflows (`cftc`, `gdelt`, `etherscan-whales`, `onchain-staking`) clear the retry prefix (`retry=()`) in backfill mode; only the scheduled incremental path retries.
  - **SEC's soft-failure collectors (`sec_form4`, `sec_form13f`) are not wrapped** — they already tolerate per-item 404s and record partials without failing the run, so a retry would re-walk for no gain.

### Discord webhook secret

Real-time alerts post to a Discord channel via an incoming webhook. The webhook URL is a repo secret named **`DISCORD_WEBHOOK_URL`**; it is **not** a local/CLI variable (the `genkei` tool never uses it), so it lives only as a GitHub Actions secret, not in `.env`.

To configure:

1. Discord → Server Settings → Integrations → Webhooks → New Webhook; pick the channel; copy the webhook URL.
2. GitHub → repo Settings → Secrets and variables → Actions → New repository secret, name `DISCORD_WEBHOOK_URL`, paste the URL.

Until the secret is set, the shared `discord-notify` action (`.github/actions/discord-notify`) no-ops gracefully — the GitHub issues still get raised, so nothing breaks; you just don't get the real-time ping. A non-2xx response from Discord is logged as a warning, never a job failure (the issue remains the durable record).

## Local development

```bash
# .env (gitignored)
GENKEI_DATABASE_URL=postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>
```

Then:

```bash
.venv/bin/alembic upgrade head        # apply migrations
.venv/bin/python -c "from genkei.common.db import connection
with connection() as conn, conn.cursor() as cur:
    cur.execute('SELECT current_database(), version()'); print(cur.fetchone())"
```

## Cockpit read API

The FastAPI read layer (`genkei-api`, `src/genkei/api/`) runs as its own
container on `mission_control_net` alongside `genkeicapital-postgres`. It is
**read-only** and **LAN-only** (no `cloudflared` route in v1). Its
docker-compose service definition, exposure/auth posture, resource-protection
ceilings (small pool so it can't starve ingest), and the `/health` +
Discord/issue alerting wiring live in **`docs/api-deployment.md`**.

## Backups

See `docs/backups.md` — full posture, retention scheme, restore runbook, and the 2026-05-22 restore drill evidence. The scripts live in `infra/backups/`.

## What's NOT in this doc (intentionally)

- The actual IP address of the Beelink — use `<beelink-host>` everywhere committed.
- SSH keys, passwords, Postgres credentials.
- Any URL of services not relevant to this project.
- Topology of unrelated services running on the Beelink — see `/server-info` for the full picture.
