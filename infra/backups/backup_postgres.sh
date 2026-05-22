#!/usr/bin/env bash
# Nightly pg_dump of genkeicapital-postgres (B-070).
#
# Designed to run on the Beelink that hosts the container (so the dump
# is taken from inside the container's network namespace via `docker
# exec`). Output goes to ${BACKUP_DIR:-$HOME/homelab-backups/genkei}/
# with retention managed in-place. See docs/backups.md for the full
# posture + restore runbook.
#
# Usage:
#   ./backup_postgres.sh              # nightly run
#   BACKUP_DIR=/mnt/foo ./backup_postgres.sh
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

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DOW=$(date -u +%u)   # 1=Mon..7=Sun
DOM=$(date -u +%d)
DUMP_FILE="$BACKUP_DIR/daily/genkei_capital_${TIMESTAMP}.pgcustom"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
die() { log "FATAL: $*" >&2; exit "${2:-1}"; }

# --- Preflight ----------------------------------------------------------------

docker inspect "$CONTAINER" >/dev/null 2>&1 \
  || die "container $CONTAINER is not running"

mkdir -p "$BACKUP_DIR"/{daily,weekly,monthly}

# Fail fast if the volume the backups land on has less free space than
# 3x the live DB size (roughly enough headroom for the new dump + the
# old ones in the retention window).
LIVE_BYTES=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT pg_database_size(current_database())")
FREE_BYTES=$(df -B1 --output=avail "$BACKUP_DIR" | tail -1)
NEEDED=$((LIVE_BYTES * 3))
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
docker exec "$CONTAINER" pg_dump \
  -U "$DB_USER" -d "$DB_NAME" \
  --format=custom --no-owner \
  --file=/tmp/genkei_dump.pgcustom \
  || die "pg_dump failed" 2

docker cp "$CONTAINER":/tmp/genkei_dump.pgcustom "$DUMP_FILE"
docker exec "$CONTAINER" rm -f /tmp/genkei_dump.pgcustom

END=$(date +%s)
SIZE=$(du -h "$DUMP_FILE" | cut -f1)
log "dump complete in $((END - START))s, size=$SIZE"

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

log "backup OK"
