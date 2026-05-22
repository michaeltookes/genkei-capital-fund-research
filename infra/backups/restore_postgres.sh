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
#   ./restore_postgres.sh <dump_file>                    # default port 5499
#   PORT=5500 NAME=foo ./restore_postgres.sh <dump_file>
#
# Exit codes:
#   0  restore succeeded + parity checks pass
#   1  prerequisite failure (dump missing, docker missing)
#   2  pg_restore failed
#   3  row-count parity check FAILED (dump is suspect)
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <dump_file>" >&2
  exit 1
fi

DUMP_FILE="$1"
NAME="${NAME:-genkei-restore-drill}"
PORT="${PORT:-5499}"
PASSWORD="${PASSWORD:-drill_$(date +%s)}"
IMAGE="${IMAGE:-timescale/timescaledb:latest-pg16}"

[ -f "$DUMP_FILE" ] || { echo "dump file not found: $DUMP_FILE" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker not available" >&2; exit 1; }

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

cleanup() {
  if [ "${KEEP_CONTAINER:-0}" = "1" ]; then
    log "container kept as $NAME (port $PORT, password $PASSWORD)"
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

for i in $(seq 1 30); do
  if docker exec "$NAME" pg_isready -U genkei_capital -d genkei_capital -q; then
    log "container ready after ${i}s"
    break
  fi
  sleep 1
done

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

docker exec "$NAME" psql -U genkei_capital -d genkei_capital -c \
  "SELECT timescaledb_post_restore()" >/dev/null

# --- Parity check against live (if reachable) --------------------------------
#
# Sample a handful of high-cardinality tables. If the live container is
# reachable we compare row counts to flag a suspect dump immediately.
# If it isn't (DR scenario where the live container is gone), we skip
# the comparison and just report the restored counts.

TABLES=(sec.filings sec.form4_transactions meta.raw_blobs sec.form4_normalized_filings defillama.stablecoins coingecko.market_data meta.ingest_runs meta.signals)

mismatches=0
log "parity check across ${#TABLES[@]} tables:"
for t in "${TABLES[@]}"; do
  restored=$(docker exec "$NAME" psql -U genkei_capital -d genkei_capital -tAc \
    "SELECT count(*) FROM $t" 2>/dev/null || echo "ERR")
  if docker inspect genkeicapital-postgres >/dev/null 2>&1; then
    live=$(docker exec genkeicapital-postgres psql -U genkei_capital -d genkei_capital -tAc \
      "SELECT count(*) FROM $t" 2>/dev/null || echo "ERR")
    if [ "$live" = "$restored" ]; then
      printf "  %-40s live=%-10s restored=%-10s OK\n" "$t" "$live" "$restored"
    else
      printf "  %-40s live=%-10s restored=%-10s MISMATCH\n" "$t" "$live" "$restored"
      mismatches=$((mismatches + 1))
    fi
  else
    printf "  %-40s restored=%s (live not reachable)\n" "$t" "$restored"
  fi
done

if [ "$mismatches" -gt 0 ]; then
  log "FAIL: $mismatches table(s) mismatched between live and restored"
  exit 3
fi

log "restore OK"
log "to inspect: docker exec -it $NAME psql -U genkei_capital -d genkei_capital"
log "to keep the container after this script exits, re-run with KEEP_CONTAINER=1"
