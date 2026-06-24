#!/usr/bin/env bash
#
# retry.sh — run a command with bounded retries and exponential backoff (B-125).
#
# Usage:
#   bash scripts/ci/retry.sh <max_attempts> <base_delay_seconds> -- <command> [args...]
#
# Runs <command>. On a non-zero exit it waits base_delay * 2^(n-1) seconds and
# retries, up to <max_attempts> total attempts. Exits 0 on the first success,
# or with the final attempt's exit code once all attempts are exhausted.
#
# Why this exists: each daily ingest workflow runs its collector once. A single
# transient API flake (a 502 from DeFiLlama, a slow FRED at 11:00 UTC) used to
# cost a full day of data until the next cron. This wraps only the external-API
# *collect* step so a flake is retried instead of dropping a day.
#
# Idempotency contract: the collectors upsert and wrap their writes in a single
# meta.ingest_runs row, so a retried attempt does not corrupt accounting — a
# failed attempt's run is marked failed and a fresh run is opened on retry.
#
# Run-id capture: the command's stdout/stderr stream through unchanged. A failed
# attempt prints no `ingest_run_id=` line (collectors print it only on success),
# so a caller that does `grep ingest_run_id= | tail -n1` over the combined
# output of all attempts still recovers the *successful* attempt's id. Retry
# diagnostics go to stderr so they never pollute captured stdout.
#
# Deliberately a plain bash script, not a third-party Action — matches the
# repo's SHA-pinned-action posture (nothing new to pin or trust).

set -uo pipefail

if [ "$#" -lt 4 ]; then
  echo "retry.sh: usage: retry.sh <max_attempts> <base_delay_seconds> -- <command...>" >&2
  exit 2
fi

max_attempts="$1"
base_delay="$2"
shift 2

if [ "$1" != "--" ]; then
  echo "retry.sh: expected '--' before the command, got: $1" >&2
  exit 2
fi
shift

case "$max_attempts" in
  '' | *[!0-9]*) echo "retry.sh: max_attempts must be a positive integer, got: $max_attempts" >&2; exit 2 ;;
esac
case "$base_delay" in
  '' | *[!0-9]*) echo "retry.sh: base_delay must be a non-negative integer, got: $base_delay" >&2; exit 2 ;;
esac
if [ "$max_attempts" -lt 1 ]; then
  echo "retry.sh: max_attempts must be >= 1, got: $max_attempts" >&2
  exit 2
fi

attempt=1
while true; do
  # Capture the command's real exit status directly — an `if "$@"; then`
  # construct would report 0 here when the command fails (the not-taken
  # `then` branch leaves `$?` at 0).
  "$@"
  status=$?
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "retry.sh: '$1' failed after ${attempt} attempt(s) (last exit ${status})" >&2
    exit "$status"
  fi
  delay=$(( base_delay * 2 ** (attempt - 1) ))
  echo "retry.sh: attempt ${attempt}/${max_attempts} of '$1' failed (exit ${status}); retrying in ${delay}s" >&2
  sleep "$delay"
  attempt=$(( attempt + 1 ))
done
