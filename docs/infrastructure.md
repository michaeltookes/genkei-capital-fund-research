# Infrastructure

How this repo connects to the homelab Postgres that backs the data lake.

> **Source of truth:** the user's `/server-info` skill (local-only, never committed). Detailed credentials and absolute IPs live there. This doc captures only what's safe to share inside the repo, using placeholders.

## Postgres

**Container:** `genkeicapital-postgres`
**Image:** `timescale/timescaledb:latest-pg16`
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

**Decision (2026-05-09):** the homelab container runs `timescale/timescaledb:latest-pg16`. The repo migration that activates the extension is already committed (`migrations/versions/20260509_install_timescaledb_extension.py`).

### Container swap (manual step on the homelab)

```bash
ssh <beelink-host>
cd ~/homelab/apps/mission-control/genkei-capital/postgres/

# Edit docker-compose.yml: replace `image: postgres:16-alpine` with
# `image: timescale/timescaledb:latest-pg16`. Same data dir, same port,
# same network — drop-in replacement.

docker compose down
docker compose up -d
docker compose logs -f genkeicapital-postgres   # confirm it's healthy
```

Then, from a developer machine pointed at the new container:

```bash
.venv/bin/alembic upgrade head
```

The activation migration runs `CREATE EXTENSION IF NOT EXISTS timescaledb`. If the swap didn't happen, that statement fails loudly — by design, no silent degradation.

### Fallback

If the swap turns out to be impractical (license concerns, homelab compatibility issue), back out by reverting the migration's effect (drop the extension on the database) and switch to `pg_partman` + hand-written rollups per the alternative path in `docs/storage.md`. Documented but not pursued unless TimescaleDB itself becomes a problem.

### Hypertable activation

Per `docs/storage.md`, hypertables live in their own migrations, separate from the base table DDL. Each per-source schema migration creates plain tables; a follow-up migration in the same series promotes the time-series tables to hypertables via `SELECT create_hypertable(...)`. This keeps the tables usable on plain PG even before TimescaleDB activates.

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
