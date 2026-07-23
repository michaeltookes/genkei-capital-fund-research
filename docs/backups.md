# Postgres Backup Posture

**B-070 / B-138.** Backup + restore strategy for `genkeicapital-postgres`. The data lake is the asset; this doc + the scripts in `infra/backups/` are what defend it.

> **Status as of 2026-07-22 (B-138):** The scripts now ship the full posture — local nightly dump, **off-site mirror** (rclone → R2, gated on `OFFSITE_REMOTE`), **Discord failure alerts**, and a **`meta.backup_runs` heartbeat** that `backup-staleness-check.yml` monitors so a silently-stopped cron pages. What remains is a **one-time manual step on the Beelink** — the homelab isn't modified autonomously from CI (per `~/.claude/skills/server-info/` governance): install the cron, drop in `rclone.conf` for R2, set `OFFSITE_REMOTE` + `DISCORD_WEBHOOK_URL`, and confirm the first heartbeat row lands. See [Install on the Beelink](#install-on-the-beelink). **Until that first heartbeat exists, `backup-staleness-check.yml` will (correctly) page daily that no backup is recorded** — that alert is the acceptance gate for "backups are actually running," not noise to silence.

## What's at risk

| Asset | Today's volume | Replaceable? |
|---|---|---|
| Raw DefiLlama / CoinGecko / FRED / SEC blobs (`meta.raw_blobs`) | 39k rows, ~8.5y backfill depth | **Re-fetchable** but expensive — multi-day re-backfill, rate-limit risk, and any vendor-side history truncation is irrecoverable. |
| Normalized per-source tables (defillama, coingecko, fred, sec, onchain, protocol_*) | ~1.5M rows total | **Derivable** from raw blobs IF blobs survived. |
| Analytics views | 0 stored bytes — view definitions only | Trivial: re-`alembic upgrade head`. |
| Research decisions (`docs/research/decisions/*.md`) | ~12 files | **In git** — already off-site. |
| **Scoring signals (`meta.signals`)** | 35 rows × 1/day onward | **Irreplaceable** — the rubric records a daily snapshot of conditions that can't be re-derived later. This is the asset that just started compounding today. |
| Ingest audit trail (`meta.ingest_runs`) | 104 rows | **Irreplaceable** — losing this means losing the provenance trio CLAUDE.md commits to. |

Three classes of failure to defend against:

1. **Disk failure / volume corruption on the Beelink** — the Docker volume `genkeicapital_postgres_data` is on a single physical disk. SSD failure or filesystem corruption loses everything in a single event.
2. **Operator error** — accidental `DROP SCHEMA`, a broken Alembic migration that does irreversible damage before the failure is noticed, an ingester bug that silently truncates a table.
3. **Beelink loss** — physical theft, fire, flood, or the same kind of "the whole homelab is dead" event a single-machine setup can't recover from.

Local dumps defend against (1) and (2). Off-site copies are the only defense against (3).

## Strategy

| Layer | What | Where | When |
|---|---|---|---|
| Local nightly dump | `pg_dump --format=custom` of the genkei DB | `~/homelab-backups/genkei/daily/` on the Beelink | 04:00 UTC daily (after the ingest cron window) |
| Local retention (tiered, hard-linked) | 7 daily + 4 weekly + 12 monthly | same volume | rotated by `backup_postgres.sh` each run |
| Off-site copy | Whichever target the user picks — see [Off-site](#off-site) | external to the Beelink | nightly, after the dump completes |
| Quarterly restore drill | `restore_postgres.sh <latest_dump>` against an isolated container | Beelink | every 3 months, logged to `docs/research/decisions/` |

### Why `pg_dump` over volume snapshots

The existing `~/homelab/scripts/backup.sh` uses `docker run alpine tar czf` on the live Postgres volume. That's risky for Postgres: tarring a live data directory gives you an inconsistent file-system snapshot (WAL mid-write, dirty buffers not flushed) that may or may not replay cleanly. `pg_dump` is a logical dump — internally consistent regardless of write activity — and it's the procedure TimescaleDB documents for backups.

Volume snapshots are a fine *supplement* (faster restore for the "Beelink just rebooted, the volume is fine" case) but not a *replacement* for logical dumps.

### Why custom format

`pg_dump --format=custom` is the only format that:

- Supports parallel `pg_restore -j N` (we don't use it today but the option's there for free).
- Lets `pg_restore --list` show the TOC without unpacking — useful for selective restore and for the script's corruption sanity-check.
- Compresses by default (~3:1 on the genkei DB: 1.5 GB → 511 MB).

Plain SQL is human-readable but ~3x larger and can't be selectively restored. `directory` format is useful for very large DBs (50 GB+) where parallel dump matters; not today.

### Retention math

- **Daily × 7** — covers "broken migration two days ago" recovery without losing more than a day of data.
- **Weekly × 4** — covers "we just noticed a silent data corruption from three weeks ago" without burning daily slots.
- **Monthly × 12** — covers "we need to see what the data looked like 8 months ago for a paper trail" without blowing disk.

Weekly and monthly tiers are **hard-links** of the Sunday/1st-of-month daily dump. Same file, three directory entries. Disk usage is `(daily count) × (avg dump size)`, currently ~7 × 511 MB ≈ 3.5 GB total for local retention. Beelink has 65 GB free; we'd need to grow the lake by 18x before this exceeds 10% of available disk.

## Off-site

**Decision (B-138): Cloudflare R2.** `backup_postgres.sh` now performs an additive `rclone copyto` of each dump to `$OFFSITE_REMOTE` (e.g. `r2:genkei-backups`) right after the local dump verifies, mirroring the same daily/weekly/monthly tiering. It's **additive only** — the script never deletes remote objects, so off-site retention is an R2 bucket-lifecycle rule, not something a local-dir bug could wipe. The step is gated on `OFFSITE_REMOTE`: unset, the script runs local-dump-only exactly as before, so it stays installable before R2 is configured. The comparison below is retained for the record.

| Option | Cost | Latency to setup | Failure mode |
|---|---|---|---|
| **Cloudflare R2** | $0.015/GB-mo storage, **$0 egress** | ~30 min (R2 account already adjacent to existing Cloudflare tunnel infra) | Account lockout would block restore; egress-free recovery beats every alternative once you do need it. |
| **Backblaze B2** | $0.005/GB-mo storage, $0.01/GB egress | ~30 min | Cheapest at current volumes; egress hurts during a full restore (~$5 to pull 12mo of monthlies). |
| **rsync to dev-pi** | Free | ~10 min if Pi has space + key-auth set up | Same building → same fire / theft event takes both. Defends against Beelink-only events, not site-wide. |
| **AWS S3 Glacier** | $0.004/GB-mo storage, multi-hour retrieval | ~45 min | Cheap but recovery time is hours-to-day. Wrong tier for "I broke prod, undo." |
| **External USB drive, rotated weekly** | One-time hardware cost | Manual process | Defends against site-wide events only if the drive is actually carried off-site every week. Easy to skip. |

**Recommendation:** Cloudflare R2 because the existing `cloudflared` container on the Beelink means the auth + connectivity story is already familiar, and $0 egress means a full restore won't be priced by panic. Cost at current volume: ~3.5 GB × $0.015 = **$0.05/month**. At 50x growth, ~$2.50/month.

Once a target is picked, the upload step gets added to `backup_postgres.sh` after the local dump completes. Until that decision lands, **the script ships local-dump-only** so it's installable today and the off-site layer follows independently.

## Install on the Beelink

The scripts live in this repo; installing them on the Beelink is a one-time manual step (per `~/.claude/skills/server-info/` governance, I don't modify the homelab autonomously).

```bash
# On the Beelink:
ssh michael@<beelink-host>
mkdir -p ~/homelab/scripts/genkei-backups
cd ~/homelab/scripts/genkei-backups

# Pull the scripts from the repo:
curl -sSL https://raw.githubusercontent.com/michaeltookes/genkei-capital-fund-research/main/infra/backups/backup_postgres.sh -o backup_postgres.sh
curl -sSL https://raw.githubusercontent.com/michaeltookes/genkei-capital-fund-research/main/infra/backups/restore_postgres.sh -o restore_postgres.sh
chmod +x backup_postgres.sh restore_postgres.sh

# One-time off-site setup (Cloudflare R2, B-138):
#   Create the bucket + an R2 API token, then configure an rclone remote
#   named `r2` of type `s3` (provider=Cloudflare) with that token. See
#   https://rclone.org/s3/#cloudflare-r2. A bucket lifecycle rule handles
#   off-site retention (the script only ever adds objects, never deletes).
rclone lsd r2:genkei-backups   # sanity: remote reachable

# One-shot test run (with off-site + alerting env wired):
export OFFSITE_REMOTE="r2:genkei-backups"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/…"   # same webhook as CI
./backup_postgres.sh
ls -lh ~/homelab-backups/genkei/daily/
rclone ls r2:genkei-backups/daily/                               # off-site copy present?

# Confirm the heartbeat landed (this is what backup-staleness-check.yml reads):
genkei query 'SELECT finished_at, offsite_status, dump_bytes FROM meta.backup_runs ORDER BY finished_at DESC LIMIT 1'

# Install the cron (04:00 UTC daily). Cron has a bare environment, so pass
# the two vars inline; keep the webhook out of shell history / world-readable
# crontabs by sourcing a 600-perm env file instead if you prefer.
( crontab -l 2>/dev/null; echo '0 4 * * * OFFSITE_REMOTE=r2:genkei-backups DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/… ~/homelab/scripts/genkei-backups/backup_postgres.sh >> /tmp/genkei-backup.log 2>&1' ) | crontab -

# Verify:
crontab -l | grep backup_postgres
```

**Alerting (B-138).** Failures now surface two ways, both through the existing B-119 channels:

- **Ran-and-errored** — the script posts a red embed to `DISCORD_WEBHOOK_URL` on any non-zero exit (an `EXIT` trap, so it fires for `pg_dump` failures, disk-preflight failures, and off-site-upload failures alike). It also still writes the error to `/tmp/genkei-backup.log`.
- **Silently stopped** — the cron being removed, the Beelink being down, or the script dying before it can even post leaves *no fresh `meta.backup_runs` row*. `.github/workflows/backup-staleness-check.yml` runs daily on the self-hosted runner, reads the newest heartbeat over `mission_control_net`, and opens a GitHub issue + Discord ping when it's older than 25h (or missing). This is the backup-side twin of `ingest-staleness-check.yml`, and it's registered in `workflow-failure-alert.yml` so the check's *own* failures page too.

## Restore runbook

Two scenarios:

### Scenario A — Disaster recovery (production restore)

The genkei container is broken or its volume is corrupt. You want to bring the production container back from a dump.

1. **Stop the ingest pipeline first.** GitHub → Actions → DefiLlama Daily / CoinGecko Daily / FRED Daily / SEC Daily → Disable workflow. This prevents an ingester writing to the half-restored DB.
2. **Snapshot the broken state, don't delete it.** Even if the volume is corrupt, it might hold a few hours of writes that the latest dump doesn't:
   ```bash
   docker stop genkeicapital-postgres
   sudo tar czf ~/genkei-volume-broken-$(date +%s).tar.gz \
     -C /var/lib/docker/volumes/genkeicapital_postgres_data .
   ```
3. **Recreate the container with a fresh volume.**
   ```bash
   cd ~/homelab/apps/mission-control/genkei-capital/postgres/
   docker compose down
   docker volume rm genkeicapital_postgres_data
   docker compose up -d
   # wait for healthy:
   until docker exec genkeicapital-postgres pg_isready -U genkei_capital -d genkei_capital -q; do sleep 1; done
   ```
4. **Run the restore.** This is the same procedure `restore_postgres.sh` automates, applied to the *real* container:
   ```bash
   DUMP=~/homelab-backups/genkei/daily/$(ls -t ~/homelab-backups/genkei/daily/ | head -1)

   docker exec genkeicapital-postgres psql -U genkei_capital -d genkei_capital \
     -c "CREATE EXTENSION IF NOT EXISTS timescaledb"
   docker exec genkeicapital-postgres psql -U genkei_capital -d genkei_capital \
     -c "SELECT timescaledb_pre_restore()"
   docker cp "$DUMP" genkeicapital-postgres:/tmp/dump.pgcustom
   docker exec genkeicapital-postgres pg_restore \
     -U genkei_capital -d genkei_capital \
     --no-owner --single-transaction --exit-on-error /tmp/dump.pgcustom
   docker exec genkeicapital-postgres psql -U genkei_capital -d genkei_capital \
     -c "SELECT timescaledb_post_restore()"
   ```
5. **Sanity-check.** From any dev machine:
   ```bash
   genkei watchlist health   # all sources OK?
   ```
6. **Re-enable the ingest workflows.**

### Scenario B — Restore drill (quarterly verification)

Run from the Beelink to confirm the latest dump is restorable. Doesn't touch production.

```bash
DUMP=~/homelab-backups/genkei/daily/$(ls -t ~/homelab-backups/genkei/daily/ | head -1)
~/homelab/scripts/genkei-backups/restore_postgres.sh "$DUMP"
```

The script:
1. Spins up an isolated `genkei-restore-drill` container on port 5499 with a temporary volume.
2. Runs the TimescaleDB-aware restore procedure.
3. Compares row counts in 8 high-cardinality tables against the live container.
4. Reports OK / FAIL; tears down the drill container on exit (unless `KEEP_CONTAINER=1`).

Log the result + date in a research decision so the next quarter knows the last drill date.

## Drill evidence (2026-05-22)

The procedure documented above was end-to-end verified on 2026-05-22 against the live homelab DB during the B-070 ship. Captured numbers:

| Metric | Value |
|---|---|
| Live DB size | 1,531 MB |
| Compressed dump size | 511 MB (3.0× compression) |
| pg_dump duration | 73s |
| pg_restore duration into fresh container | 86s |
| Restored DB size | 1,448 MB (within 5% of live; expected — no index bloat) |
| Hypertables restored | 9 |
| Hypertable chunks restored | 1,094 |
| Tables checked for row-count parity | 8 |
| Parity matches | 8 of 8 |
| Parity mismatches | 0 |
| TimescaleDB extension restored | yes (`CREATE EXTENSION` → `pre_restore()` → `pg_restore` → `post_restore()`) |

Sample parity table:

| Table | Live rows | Restored rows | Match |
|---|---|---|---|
| `sec.filings` | 213,713 | 213,713 | ✓ |
| `sec.form4_transactions` | 192,323 | 192,323 | ✓ |
| `meta.raw_blobs` | 39,524 | 39,524 | ✓ |
| `sec.form4_normalized_filings` | 36,707 | 36,707 | ✓ |
| `defillama.stablecoins` | 930,962 | 930,962 | ✓ |
| `coingecko.market_data` | 7,056 | 7,056 | ✓ |
| `meta.ingest_runs` | 104 | 104 | ✓ |
| `meta.signals` | 35 | 35 | ✓ |

Including the brand-new `meta.signals` from today's B-065 ship — the asset that just started compounding survived the round-trip.

## Caveats found during the drill

| Surface | Note |
|---|---|
| `pg_dump` warnings | TimescaleDB's `hypertable`, `chunk`, and `continuous_agg` catalog tables have circular FK constraints. `pg_dump` warns on stdout; the warnings are expected and don't indicate a problem. `--disable-triggers` is **not** needed when using the `pre_restore()`/`post_restore()` procedure. |
| Container image drift | `docker ps` reports the image as `timescale/timescaledb:latest-pg16`, not the `2.26.4-pg16` pinned in `docs/infrastructure.md`. The compose file on the Beelink likely uses the `latest` tag. Not a backup issue but worth flagging — `latest` means restores into a different version-pinned target may need attention. (Filed separately if you want to pin it.) |
| Postgres user | The container's superuser is `genkei_capital`, not `postgres`. The dump+restore scripts hardcode this — change `DB_USER` if the deployment ever moves to a different role. |
| Disk preflight | The backup script refuses to run if free disk is less than 3× live DB. At 1.5 GB DB, that's 4.5 GB needed. Beelink has 65 GB free today. Headroom check stays meaningful as the lake grows. |

## Open items

- ✅ **Off-site target picked + wired** (B-138) — Cloudflare R2 via `rclone copyto`, gated on `OFFSITE_REMOTE`. See [Off-site](#off-site).
- ✅ **Backup-failure alerting** (B-138) — Discord post on failure + `meta.backup_runs` heartbeat monitored by `backup-staleness-check.yml`. See [Alerting](#install-on-the-beelink).
- ⏳ **Install on the Beelink + confirm first heartbeat** (B-138, manual) — the code is ready; the homelab-side step (cron + `rclone.conf` + `OFFSITE_REMOTE`/`DISCORD_WEBHOOK_URL` + verifying the first `meta.backup_runs` row) is the remaining gate. `backup-staleness-check.yml` pages daily until it's done.
- ⏳ **Pin the container image.** Switch the homelab compose file from `timescale/timescaledb:latest-pg16` to the `2.26.4-pg16` documented in `docs/infrastructure.md` so restores happen against a known target version.

## Restore-drill schedule

The B-070 ship drilled the restore end-to-end on **2026-05-22** (see [Drill evidence](#drill-evidence-2026-05-22)). Quarterly cadence → **next drill due ~2026-08-22.** Run Scenario B, then log the result + date in a `docs/research/decisions/` entry so the following quarter knows the last-good date.
