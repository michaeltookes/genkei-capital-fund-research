#!/usr/bin/env bash
# Nightly pg_dump of genkeicapital-postgres (B-070, B-138).
#
# Designed to run on the Beelink that hosts the container (so the dump
# is taken from inside the container's network namespace via `docker
# exec`). Output goes to ${BACKUP_DIR:-$HOME/homelab-backups/genkei}/
# with retention managed in-place. See docs/backups.md for the full
# posture + restore runbook.
#
# Three observability hooks (B-138) make this safe to leave unattended:
#   * On any failure it posts to DISCORD_WEBHOOK_URL (the same webhook
#     the GitHub-Actions B-119 alerter uses) — the ran-and-errored case.
#   * On success it writes a heartbeat row to meta.backup_runs, which
#     backup-staleness-check.yml reads to catch the cron-silently-stopped
#     case (the runner can't see this host's filesystem, only Postgres).
#   * If OFFSITE_REMOTE is set it mirrors the dump to that rclone remote
#     (Cloudflare R2 recommended) — the only defense against Beelink loss.
#
# Usage:
#   ./backup_postgres.sh                                   # nightly run
#   BACKUP_DIR=/mnt/foo ./backup_postgres.sh
#   OFFSITE_REMOTE=r2:genkei-backups ./backup_postgres.sh  # + off-site copy
#   DISCORD_WEBHOOK_URL=https://... ./backup_postgres.sh   # + failure pings
#
# Exit codes:
#   0  success (dump written, retention applied)
#   1  prerequisite failure (container missing, disk full, etc.)
#   2  pg_dump itself failed
set -euo pipefail

CONTAINER="${CONTAINER:-genkeicapital-postgres}"
DB_USER="${DB_USER:-genkei_capital}"
DB_NAME="${DB_NAME:-genkei_capital}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/homelab-backups/genkei}"
RETAIN_DAILY="${RETAIN_DAILY:-7}"
RETAIN_WEEKLY="${RETAIN_WEEKLY:-4}"
RETAIN_MONTHLY="${RETAIN_MONTHLY:-12}"

# Off-site + alerting config. Both optional: unset keeps the historical
# local-dump-only, silent behaviour so the script stays installable before
# either is wired.
OFFSITE_REMOTE="${OFFSITE_REMOTE:-}"          # rclone remote, e.g. r2:genkei-backups
DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}" # same webhook as the B-119 alerter

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DOW=$(date -u +%u)   # 1=Mon..7=Sun
DOM=$(date -u +%d)
DUMP_FILE="$BACKUP_DIR/daily/genkei_capital_${TIMESTAMP}.pgcustom"
HOST_SHORT="$(hostname -s 2>/dev/null || echo beelink)"

# --- Failure alerting (B-138) -------------------------------------------------
#
# A single notification path: die() records why, and the EXIT trap posts to
# Discord on any non-zero exit — whether from die() or an unhandled set -e
# failure. Success sets BACKUP_OK=1 to suppress it. Mirrors the payload shape
# of .github/actions/discord-notify so the alert reads the same in the channel.

BACKUP_OK=0
FAIL_MSG=""

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
die() { FAIL_MSG="$*"; log "FATAL: $*" >&2; exit "${2:-1}"; }

notify_discord() {
  # $1=title  $2=description  $3=color(decimal, default red)
  [ -n "$DISCORD_WEBHOOK_URL" ] || return 0
  local payload
  payload="$(TITLE="$1" DESCRIPTION="$2" COLOR="${3:-15158332}" python3 - <<'PY' 2>/dev/null || true
import json, os
print(json.dumps({"embeds": [{
    "title": os.environ["TITLE"][:256],
    "description": os.environ["DESCRIPTION"][:4096],
    "color": int(os.environ.get("COLOR") or "15158332"),
}]}))
PY
)"
  [ -n "$payload" ] || return 0
  # --max-time so a slow/hung webhook can never wedge an unattended cron run.
  curl -sS --max-time 15 -o /dev/null -H 'Content-Type: application/json' \
    --data-raw "$payload" "$DISCORD_WEBHOOK_URL" || true
}

finish() {
  local rc=$?
  [ "$rc" -eq 0 ] && [ "$BACKUP_OK" -eq 1 ] && return 0
  notify_discord "🔴 genkei backup FAILED on ${HOST_SHORT}" \
    "\`${FAIL_MSG:-unexpected error (exit $rc)}\`
Nightly \`pg_dump\` of \`${DB_NAME}\` did not complete. See \`/tmp/genkei-backup.log\` on the Beelink; no heartbeat row was written to \`meta.backup_runs\` for this run." 15158332
}
trap finish EXIT

# --- Preflight ----------------------------------------------------------------

[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" = "true" ] \
  || die "container $CONTAINER is not running"

mkdir -p "$BACKUP_DIR"/{daily,weekly,monthly}

# Fail fast if the volume the backups land on has less free space than
# DISK_FACTOR_PCT% of the live DB size (default 300% — headroom for the
# new dump + the retention window). The 300 default assumes ~3:1 dump
# compression and ~7 daily slots; override when the ratio diverges —
# e.g. a raw_blobs-heavy DB (2026-08: 32 of 34 GB is JSONB whose dump
# compresses far below live size) can run with DISK_FACTOR_PCT=100 and
# a shorter RETAIN_DAILY on a tight disk. See B-140.
DISK_FACTOR_PCT="${DISK_FACTOR_PCT:-300}"
LIVE_BYTES=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT pg_database_size(current_database())")
FREE_BYTES=$(df -B1 --output=avail "$BACKUP_DIR" | tail -1)
NEEDED=$((LIVE_BYTES * DISK_FACTOR_PCT / 100))
if [ "$FREE_BYTES" -lt "$NEEDED" ]; then
  die "insufficient disk: need >$((NEEDED / 1024 / 1024)) MB, have $((FREE_BYTES / 1024 / 1024)) MB"
fi

# --- Dump ---------------------------------------------------------------------

log "dumping $DB_NAME from $CONTAINER → $(basename "$DUMP_FILE")"
START=$(date +%s)

# Custom format is required for the restore procedure (parallel apply,
# selective restore, TOC inspection without unpacking the archive).
# --no-owner so the dump replays cleanly into a fresh container whose
# postgres user may differ from production.
#
# EXCLUDE_TABLE_DATA (optional, space-separated table names): each named
# table keeps its schema in the dump but skips its rows. Deployed on the
# Beelink as EXCLUDE_TABLE_DATA=meta.raw_blobs — the 32 GB re-fetchable
# blob archive would otherwise make the nightly dump ~20.7 GB / 53 min
# (measured 2026-08-03), which the Beelink disk can't hold at 7-day
# retention. Blob data is covered separately by backup_blobs.sh (weekly,
# streamed to R2). See docs/backups.md "Split posture" + B-140.
EXCLUDE_TABLE_DATA="${EXCLUDE_TABLE_DATA:-}"
EXCLUDE_ARGS=()
for tbl in $EXCLUDE_TABLE_DATA; do
  EXCLUDE_ARGS+=("--exclude-table-data=$tbl")
done

docker exec "$CONTAINER" pg_dump \
  -U "$DB_USER" -d "$DB_NAME" \
  --format=custom --no-owner \
  "${EXCLUDE_ARGS[@]}" \
  --file=/tmp/genkei_dump.pgcustom \
  || die "pg_dump failed" 2

docker cp "$CONTAINER":/tmp/genkei_dump.pgcustom "$DUMP_FILE"
docker exec "$CONTAINER" rm -f /tmp/genkei_dump.pgcustom

END=$(date +%s)
DURATION=$((END - START))
DUMP_BYTES=$(stat -c%s "$DUMP_FILE" 2>/dev/null || stat -f%z "$DUMP_FILE")
SIZE=$(du -h "$DUMP_FILE" | cut -f1)
log "dump complete in ${DURATION}s, size=$SIZE"

# Quick sanity: dump TOC must parse.
docker run --rm -v "$BACKUP_DIR/daily":/d timescale/timescaledb:latest-pg16 \
  pg_restore --list "/d/$(basename "$DUMP_FILE")" >/dev/null \
  || die "dump file failed pg_restore --list (likely corrupt)" 2

# --- Promote to weekly / monthly ---------------------------------------------

# Sunday's daily promotes to weekly; the 1st of the month's daily promotes
# to monthly. We hard-link rather than copy so the same dump file is
# referenced from multiple retention tiers without doubling disk usage.
if [ "$DOW" = "7" ]; then
  ln -f "$DUMP_FILE" "$BACKUP_DIR/weekly/$(basename "$DUMP_FILE")"
  log "promoted to weekly"
fi
if [ "$DOM" = "01" ]; then
  ln -f "$DUMP_FILE" "$BACKUP_DIR/monthly/$(basename "$DUMP_FILE")"
  log "promoted to monthly"
fi

# --- Retention ----------------------------------------------------------------
#
# `ls -t` orders newest-first; tail -n +N drops the newest N-1 keepers
# and prints everything older for deletion. Safe on filenames without
# whitespace (our timestamps are ASCII-only).

prune() {
  local tier="$1" keep="$2"
  local pruned=0
  for f in $(ls -t "$BACKUP_DIR/$tier"/genkei_capital_*.pgcustom 2>/dev/null | tail -n +$((keep + 1))); do
    rm -f "$f"
    pruned=$((pruned + 1))
  done
  log "$tier: kept $keep, pruned $pruned"
}

prune daily   "$RETAIN_DAILY"
prune weekly  "$RETAIN_WEEKLY"
prune monthly "$RETAIN_MONTHLY"

# --- Off-site copy (B-138) ----------------------------------------------------
#
# Additive mirror to an rclone remote — Cloudflare R2 recommended (see
# docs/backups.md "Off-site"). Local dumps defend against disk failure and
# operator error; only an off-site copy survives Beelink loss (theft/fire).
# Gated on OFFSITE_REMOTE so the script still runs local-dump-only until the
# remote + rclone.conf are configured on the Beelink. copyto is additive: it
# never deletes remote objects, so off-site retention is a bucket-lifecycle
# concern, not something this script can accidentally wipe. Promotion days
# also place the dump under weekly/ and monthly/ so the remote mirrors the
# same tiering as local.

# A failed off-site copy is NOT a failed backup: the local dump already
# succeeded and defends against disk failure + operator error. So off-site
# failure doesn't die() — it records offsite_status='failed' in the heartbeat
# (which backup-staleness-check.yml surfaces as the softer OFFSITE_FAILED
# alert) and posts an amber warning now, distinct from the red "dump failed"
# page. This keeps "the Beelink-loss defense broke" from masquerading as
# "there is no backup."
OFFSITE_STATUS="skipped"
if [ -n "$OFFSITE_REMOTE" ]; then
  base="$(basename "$DUMP_FILE")"
  offsite_ok=1
  if ! command -v rclone >/dev/null 2>&1; then
    log "WARN: OFFSITE_REMOTE set but rclone is not installed — skipping off-site copy"
    offsite_ok=0
  else
    log "off-site: uploading $base → $OFFSITE_REMOTE/daily/"
    rclone copyto "$DUMP_FILE" "$OFFSITE_REMOTE/daily/$base" || offsite_ok=0
    if [ "$offsite_ok" = "1" ] && [ "$DOW" = "7" ]; then
      rclone copyto "$DUMP_FILE" "$OFFSITE_REMOTE/weekly/$base" || offsite_ok=0
    fi
    if [ "$offsite_ok" = "1" ] && [ "$DOM" = "01" ]; then
      rclone copyto "$DUMP_FILE" "$OFFSITE_REMOTE/monthly/$base" || offsite_ok=0
    fi
  fi
  if [ "$offsite_ok" = "1" ]; then
    OFFSITE_STATUS="uploaded"
    log "off-site: upload OK"
  else
    OFFSITE_STATUS="failed"
    log "off-site: upload FAILED — local dump is fine, Beelink-loss defense did not complete"
    notify_discord "🟠 genkei off-site backup FAILED on ${HOST_SHORT}" \
      "Local nightly dump of \`${DB_NAME}\` succeeded, but the off-site copy to \`${OFFSITE_REMOTE}\` did not. The lake is protected against disk failure but **not** against Beelink loss until this is fixed — check rclone / OFFSITE_REMOTE." 16763904
  fi
else
  log "off-site: OFFSITE_REMOTE unset — skipping (local-dump-only)"
fi

# --- Heartbeat (B-138) --------------------------------------------------------
#
# Record the successful run in meta.backup_runs so backup-staleness-check.yml
# (which reaches Postgres over the network but cannot see this host's
# filesystem) can tell the cron is alive. Best-effort: a heartbeat write
# failure must not fail an otherwise-good backup, so it only warns. The dump
# itself already succeeded and was pg_restore --list-verified above.

# NB: psql does NOT interpolate -v variables inside a -c string — the SQL
# must arrive on stdin for :var substitution to happen (found live on the
# Beelink during the 2026-08-03 install; the -c form sent literal ":started"
# to the server).
if docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -qtA \
     -v started="$START" -v finished="$END" -v bytes="$DUMP_BYTES" -v dur="$DURATION" \
     -v dumpfile="$(basename "$DUMP_FILE")" -v offsite="$OFFSITE_STATUS" -v host="$HOST_SHORT" \
     >/dev/null <<'SQL'
INSERT INTO meta.backup_runs
  (started_at, finished_at, status, dump_file, dump_bytes, duration_seconds, offsite_status, host)
VALUES
  (to_timestamp(:started), to_timestamp(:finished), 'ok', :'dumpfile', :bytes, :dur, :'offsite', :'host');
SQL
then
  log "heartbeat: wrote meta.backup_runs row (offsite=$OFFSITE_STATUS)"
else
  log "WARN: heartbeat write to meta.backup_runs failed (backup itself OK)"
fi

BACKUP_OK=1
log "backup OK"
