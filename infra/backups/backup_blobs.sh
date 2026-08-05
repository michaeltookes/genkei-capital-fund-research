#!/usr/bin/env bash
# Weekly raw-blobs dump, streamed straight to the off-site remote (B-138/B-140).
#
# Companion to backup_postgres.sh's split posture: the nightly core dump
# excludes meta.raw_blobs data (EXCLUDE_TABLE_DATA) because the blob
# archive is ~32 GB — too large for local retention on the Beelink and
# too slow (53 min) for a nightly window. This script covers that tier
# on a weekly cadence, streaming pg_dump straight into `rclone rcat` so
# it needs ZERO local disk regardless of archive size.
#
# Loss math: blobs are the *re-fetchable* provenance tier (docs/backups.md
# "What's at risk") — a Beelink-loss event costs at most one week of raw
# payloads, all of which are re-fetchable from vendors; the irreplaceable
# tables (meta.signals, meta.ingest_runs, normalized data) are covered by
# the nightly core dump.
#
# Observability: log-only, NO meta.backup_runs heartbeat — that table's
# staleness contract belongs to the nightly core dump and a weekly row
# here could mask a dead nightly cron from backup-staleness-check.yml.
# Uploads land under a temporary prefix first; only a size-validated
# archive is promoted to the final timestamped key that restore runbooks
# discover as "latest". Failures exit non-zero (cron mail / log) and the
# off-site listing gap is visible in R2.
#
# Usage (cron, Sundays 05:00 UTC — after the 04:00 core dump):
#   OFFSITE_REMOTE=r2:genkei-backups ~/homelab/scripts/genkei-backups/backup_blobs.sh
#
# Exit codes: 0 success; 1 prerequisite failure; 2 dump/upload failure.
set -euo pipefail

CONTAINER="${CONTAINER:-genkeicapital-postgres}"
DB_USER="${DB_USER:-genkei_capital}"
DB_NAME="${DB_NAME:-genkei_capital}"
BLOB_TABLE="${BLOB_TABLE:-meta.raw_blobs}"
OFFSITE_REMOTE="${OFFSITE_REMOTE:-}"
# A streamed dump that "succeeds" at a fraction of the expected size means
# a broken pipe or an empty table — treat below-floor uploads as failure.
MIN_BYTES="${MIN_BYTES:-1000000000}"   # 1 GB; archive is ~20 GB as of 2026-08

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "FATAL: $*"; exit "${2:-1}"; }

[ -n "$OFFSITE_REMOTE" ] || die "OFFSITE_REMOTE is required (e.g. r2:genkei-backups)"
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" = "true" ] \
  || die "container $CONTAINER is not running"
command -v rclone >/dev/null || die "rclone not installed"

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$OFFSITE_REMOTE/blobs/raw_blobs_${TIMESTAMP}.pgcustom"
TMP_DEST="$OFFSITE_REMOTE/blobs/_tmp/raw_blobs_${TIMESTAMP}.pgcustom.tmp"
PROMOTED=0

cleanup_failed_upload() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$PROMOTED" -ne 1 ]; then
    rclone deletefile "$TMP_DEST" >/dev/null 2>&1 || true
    rclone deletefile "$DEST" >/dev/null 2>&1 || true
  fi
}
trap cleanup_failed_upload EXIT

log "streaming $BLOB_TABLE from $CONTAINER → $TMP_DEST (temporary key, no local copy)"
START=$(date +%s)

docker exec "$CONTAINER" pg_dump \
  -U "$DB_USER" -d "$DB_NAME" \
  --format=custom --no-owner \
  --table="$BLOB_TABLE" \
  | rclone rcat "$TMP_DEST" \
  || die "streamed dump/upload failed" 2

DURATION=$(( $(date +%s) - START ))
UPLOADED=$(rclone size --json "$TMP_DEST" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
[ -n "$UPLOADED" ] || die "cannot stat uploaded object $TMP_DEST" 2
[ "$UPLOADED" -ge "$MIN_BYTES" ] \
  || die "uploaded temporary object is ${UPLOADED} bytes (< MIN_BYTES=$MIN_BYTES) — truncated stream?" 2

log "promoting validated blob archive → $DEST"
rclone moveto "$TMP_DEST" "$DEST" \
  || die "failed to promote validated blob archive to $DEST" 2
FINAL_BYTES=$(rclone size --json "$DEST" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
[ "$FINAL_BYTES" = "$UPLOADED" ] \
  || die "promoted object is ${FINAL_BYTES:-unknown} bytes; expected ${UPLOADED}" 2
PROMOTED=1

log "blob archive uploaded: $(( UPLOADED / 1024 / 1024 )) MB in ${DURATION}s"
log "OK"
