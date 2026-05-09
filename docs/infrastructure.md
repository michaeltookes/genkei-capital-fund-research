# Infrastructure

How this repo connects to the homelab Postgres that backs the data lake.

> **Source of truth:** the user's `/server-info` skill (local-only, never committed). Detailed credentials and absolute IPs live there. This doc captures only what's safe to share inside the repo, using placeholders.

## Postgres

**Container:** `genkeicapital-postgres`
**Image:** `postgres:16-alpine`
**Host (LAN):** `<beelink-host>`
**Port:** `5440`
**Docker network:** `mission_control_net`
**Compose path on the server:** `apps/mission-control/genkei-capital/postgres/`

### Connection string shape

```
postgresql+psycopg://<user>:<password>@<beelink-host>:5440/<db>
```

Set as `GENKEI_DATABASE_URL` in your local `.env` (gitignored) and as a GitHub Actions secret of the same name. The driver prefix is stripped automatically by `genkei.common.db._resolve_url` when libpq needs a plain URL.

Credentials live in `.env` files on the homelab server (never in this repo). Pull them from the server when wiring up a new dev machine; do not paste them anywhere committed.

## TimescaleDB status

The existing container runs **plain PostgreSQL 16-alpine** — it does **not** include TimescaleDB.

Per `docs/storage.md`, two paths forward:

1. **Switch the image** (recommended): replace `postgres:16-alpine` with `timescale/timescaledb:latest-pg16` on the homelab. Same data dir, same port, same network — drop-in replacement. After the swap, the first time-series migration runs `CREATE EXTENSION IF NOT EXISTS timescaledb;` and `SELECT create_hypertable(...)` on the appropriate tables.
2. **Stay on plain PG**: take the fallback from `docs/storage.md` — `pg_partman` plus hand-written rollups. Acceptable interim; revisit before the data lake outgrows it.

**Tracked in B-007.** Until that's resolved, every new time-series migration should be written so it works on plain PG (no `create_hypertable` calls); a separate Timescale-activation migration will land alongside the image swap.

## Network reachability

The Beelink is on a private LAN behind a double-NAT path (Google Nest Wifi → OPNsense → ISP). **GitHub-hosted Action runners cannot reach the homelab from the internet.**

Two options for CI:

1. **Self-hosted GH Actions runner** on the Beelink — already tracked as **B-077**. Runner has direct Docker-network access to `genkeicapital-postgres`. Lowest moving parts, no public exposure.
2. **Cloudflare TCP tunnel** to expose Postgres at a public hostname behind an Access policy. The homelab already runs `cloudflared`, so the building blocks are present. Adds blast-radius (any auth misconfig exposes the DB) and isn't necessary until we actually need cloud runners.

**Decision:** start with a self-hosted runner (B-077) when CI starts needing real-Postgres tests (B-024). Skip the tunnel for now.

For developer machines on the same LAN, direct connection to `<beelink-host>:5440` works once the password is in `.env`.

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

## What's NOT in this doc (intentionally)

- The actual IP address of the Beelink — use `<beelink-host>` everywhere committed.
- SSH keys, passwords, Postgres credentials.
- Any URL of services not relevant to this project.
- Topology of unrelated services running on the Beelink — see `/server-info` for the full picture.
