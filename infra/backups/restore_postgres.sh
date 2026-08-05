#!/usr/bin/env bash
# Restore genkeicapital-postgres from a custom-format pg_dump (B-070).
#
# Intentionally NOT a "restore over production" script — instead, it
# restores into a fresh isolated container on a non-default port so the
# restore can be tested without putting the real DB at risk. To do a
# production restore (DR scenario), see the manual procedure in
# docs/backups.md; this script's safety rails are deliberate.
#
# Usage:
#   ./restore_postgres.sh <core_dump_file> [blob_dump_file]  # default port 5499
#   PORT=5500 NAME=foo ./restore_postgres.sh <core_dump_file> [blob_dump_file]
#
# Exit codes:
#   0  restore succeeded + parity checks pass
#   1  prerequisite failure (dump missing, docker missing)
#   2  pg_restore failed
#   3  row-count parity check FAILED (dump is suspect)
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: $0 <core_dump_file> [blob_dump_file]" >&2
  exit 1
fi

DUMP_FILE="$1"
BLOB_DUMP_FILE="${2:-}"
NAME="${NAME:-genkei-restore-drill}"
PORT="${PORT:-5499}"
PASSWORD="${PASSWORD:-drill_$(date +%s)}"
IMAGE="${IMAGE:-timescale/timescaledb:latest-pg16}"

[ -f "$DUMP_FILE" ] || { echo "core dump file not found: $DUMP_FILE" >&2; exit 1; }
if [ -n "$BLOB_DUMP_FILE" ]; then
  [ -f "$BLOB_DUMP_FILE" ] || { echo "blob dump file not found: $BLOB_DUMP_FILE" >&2; exit 1; }
fi
docker info >/dev/null 2>&1 || { echo "docker not available" >&2; exit 1; }

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
die() { log "FATAL: $*" >&2; exit "${2:-1}"; }

cleanup() {
  if [ "${KEEP_CONTAINER:-0}" = "1" ]; then
    log "container kept as $NAME (port $PORT)"
  else
    docker rm -f "$NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# --- Spin up restore target ---------------------------------------------------

docker rm -f "$NAME" >/dev/null 2>&1 || true
log "starting fresh container $NAME on port $PORT"
docker run -d --name "$NAME" \
  -e POSTGRES_USER=genkei_capital \
  -e POSTGRES_PASSWORD="$PASSWORD" \
  -e POSTGRES_DB=genkei_capital \
  -p "$PORT":5432 \
  "$IMAGE" >/dev/null

ready=0
for i in $(seq 1 30); do
  if docker exec "$NAME" pg_isready -U genkei_capital -d genkei_capital -q; then
    log "container ready after ${i}s"
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" -eq 1 ] || die "restore target $NAME did not become ready within 30s"

# --- TimescaleDB pre-restore --------------------------------------------------
#
# Per the TimescaleDB restore procedure, the extension must be installed
# first, then `timescaledb_pre_restore()` called to disable triggers and
# event handlers that would otherwise interfere with the bulk insert.

docker exec "$NAME" psql -U genkei_capital -d genkei_capital -c \
  "CREATE EXTENSION IF NOT EXISTS timescaledb" >/dev/null
docker exec "$NAME" psql -U genkei_capital -d genkei_capital -c \
  "SELECT timescaledb_pre_restore()" >/dev/null

# --- Restore ------------------------------------------------------------------

docker cp "$DUMP_FILE" "$NAME":/tmp/dump.pgcustom
log "running pg_restore (this took ~86s on a 1.5GB DB during the B-070 drill)"
START=$(date +%s)
if ! docker exec "$NAME" pg_restore \
       -U genkei_capital -d genkei_capital \
       --no-owner --single-transaction --exit-on-error \
       /tmp/dump.pgcustom; then
  log "pg_restore FAILED"
  exit 2
fi
END=$(date +%s)
log "restore complete in $((END - START))s"

if [ -n "$BLOB_DUMP_FILE" ]; then
  docker cp "$BLOB_DUMP_FILE" "$NAME":/tmp/blobs.pgcustom
  log "restoring meta.raw_blobs from blob archive"
  START=$(date +%s)
  if ! docker exec "$NAME" pg_restore \
         -U genkei_capital -d genkei_capital \
         --data-only --single-transaction --exit-on-error \
         /tmp/blobs.pgcustom; then
    log "blob pg_restore FAILED"
    exit 2
  fi
  END=$(date +%s)
  log "blob restore complete in $((END - START))s"
else
  log "no blob archive supplied; skipping meta.raw_blobs parity for core-only dump"
fi

docker exec "$NAME" psql -U genkei_capital -d genkei_capital -c \
  "SELECT timescaledb_post_restore()" >/dev/null

# --- Parity check against live (if reachable) --------------------------------
#
# Sample a handful of high-cardinality core tables. If the live container
# is reachable we compare row counts to flag a suspect dump immediately.
# If it isn't (DR scenario where the live container is gone), we skip
# the comparison and just report the restored counts. meta.raw_blobs is
# checked separately because its weekly archive can legitimately lag live.

TABLES=(sec.filings sec.form4_transactions sec.form4_normalized_filings defillama.stablecoins coingecko.market_data meta.ingest_runs meta.signals)
LIVE_CONTAINER="${LIVE_CONTAINER:-genkeicapital-postgres}"
live_reachable=0
if [ "$(docker inspect -f '{{.State.Running}}' "$LIVE_CONTAINER" 2>/dev/null || true)" = "true" ] \
  && docker exec "$LIVE_CONTAINER" pg_isready -U genkei_capital -d genkei_capital -q; then
  live_reachable=1
else
  log "live container $LIVE_CONTAINER not reachable; reporting restored counts only"
fi

mismatches=0
log "parity check across ${#TABLES[@]} core tables:"
for t in "${TABLES[@]}"; do
  restored=$(docker exec "$NAME" psql -U genkei_capital -d genkei_capital -tAc \
    "SELECT count(*) FROM $t" 2>/dev/null || echo "ERR")
  if [ "$live_reachable" -eq 1 ]; then
    live=$(docker exec "$LIVE_CONTAINER" psql -U genkei_capital -d genkei_capital -tAc \
      "SELECT count(*) FROM $t" 2>/dev/null || echo "ERR")
    if [ "$live" = "ERR" ] || [ "$restored" = "ERR" ]; then
      printf "  %-40s live=%-10s restored=%-10s MISMATCH\n" "$t" "$live" "$restored"
      mismatches=$((mismatches + 1))
    elif [ "$live" = "$restored" ]; then
      printf "  %-40s live=%-10s restored=%-10s OK\n" "$t" "$live" "$restored"
    else
      printf "  %-40s live=%-10s restored=%-10s MISMATCH\n" "$t" "$live" "$restored"
      mismatches=$((mismatches + 1))
    fi
  elif [ "$restored" = "ERR" ]; then
    printf "  %-40s restored=%-10s MISMATCH\n" "$t" "$restored"
    mismatches=$((mismatches + 1))
  else
    printf "  %-40s restored=%s (live not reachable)\n" "$t" "$restored"
  fi
done

if [ -n "$BLOB_DUMP_FILE" ]; then
  log "blob archive check:"
  restored=$(docker exec "$NAME" psql -U genkei_capital -d genkei_capital -tAc \
    "SELECT count(*) FROM meta.raw_blobs" 2>/dev/null || echo "ERR")
  if [ "$restored" = "ERR" ] || [ "$restored" = "0" ]; then
    printf "  %-40s restored=%-10s MISMATCH\n" "meta.raw_blobs" "$restored"
    mismatches=$((mismatches + 1))
  elif [ "$live_reachable" -eq 1 ]; then
    live=$(docker exec "$LIVE_CONTAINER" psql -U genkei_capital -d genkei_capital -tAc \
      "SELECT count(*) FROM meta.raw_blobs" 2>/dev/null || echo "ERR")
    if [ "$live" = "ERR" ]; then
      printf "  %-40s live=%-10s restored=%-10s MISMATCH\n" "meta.raw_blobs" "$live" "$restored"
      mismatches=$((mismatches + 1))
    elif [ "$live" = "$restored" ]; then
      printf "  %-40s live=%-10s restored=%-10s OK\n" "meta.raw_blobs" "$live" "$restored"
    else
      printf "  %-40s live=%-10s restored=%-10s OK (weekly archive cadence)\n" "meta.raw_blobs" "$live" "$restored"
    fi
  else
    printf "  %-40s restored=%s (live not reachable)\n" "meta.raw_blobs" "$restored"
  fi
fi

if [ "$mismatches" -gt 0 ]; then
  log "FAIL: $mismatches table(s) mismatched between live and restored"
  exit 3
fi

log "restore OK"
log "to inspect: docker exec -it $NAME psql -U genkei_capital -d genkei_capital"
log "to keep the container after this script exits, re-run with KEEP_CONTAINER=1"
