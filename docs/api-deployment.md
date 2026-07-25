# Cockpit read-API deployment & exposure

How the FastAPI read layer (**B-131**, `src/genkei/api/`) is deployed on the
homelab and what its exposure posture is (**B-137**). This is the HTTP sibling
of the MCP server (B-130): same lake, read-only, but a long-lived web service
the E-002 cockpit frontend (B-132, built next) calls.

> **Source of truth for host/network specifics:** the user's `/server-info`
> skill (local-only). This doc uses `<beelink-host>` placeholders and captures
> only what's safe to commit. See `docs/infrastructure.md` for the Postgres +
> network picture this builds on.

## Service at a glance

| Property | Value |
|---|---|
| Service name | `genkei-api` |
| Image | built from this repo (`pip install -e ".[api]"`), runs `genkei-api` |
| Command / entry point | `genkei-api` → `uvicorn` (see `src/genkei/api/server.py`) |
| Docker network | `mission_control_net` (same as `genkeicapital-postgres`) |
| Container port | `8848` (`GENKEI_API_PORT`) |
| Bind inside container | `0.0.0.0` (`GENKEI_API_HOST`) — see [Exposure](#exposure--auth-posture) |
| Restart policy | `unless-stopped` |
| Logs | container stdout/stderr → `docker compose logs genkei-api` (host journald via the Docker json-file driver) |
| DB access | `db.connection()` over `mission_control_net`, `GENKEI_DATABASE_URL` |

## docker-compose service definition

Lands in the same compose project as the Postgres container on the Beelink
(`apps/mission-control/genkei-capital/`). Illustrative — real credentials live
in the server-side `.env`, never here:

```yaml
services:
  genkei-api:
    build: .                      # repo root; installs .[api]
    image: genkei-api:latest
    container_name: genkei-api
    command: ["genkei-api"]
    restart: unless-stopped
    networks:
      - mission_control_net
    environment:
      # Resolves genkeicapital-postgres by container name on the shared net.
      GENKEI_DATABASE_URL: ${GENKEI_DATABASE_URL}
      GENKEI_API_HOST: "0.0.0.0"
      GENKEI_API_PORT: "8848"
      GENKEI_API_MAX_POOL_SIZE: "4"
    # LAN-only: publish to the Beelink's LAN interface, NOT 0.0.0.0 on the host.
    # Bind the published port to the host's LAN IP so it is reachable from the
    # home network but never from the public path. (Placeholder IP — real value
    # in server-info.)
    ports:
      - "<beelink-lan-ip>:8848:8848"
    depends_on:
      - genkeicapital-postgres

networks:
  mission_control_net:
    external: true
```

Deploy / restart, from the compose dir on the Beelink:

```bash
docker compose up -d --build genkei-api
docker compose logs -f genkei-api          # confirm startup + /health
```

## Exposure / auth posture

**v1 is local-network-only. No public exposure. No authentication.** The
cockpit is a single-user, LAN-bound tool; the data is already committed to the
repo under `reports/` and derived from free public sources, so the threat model
is "don't accidentally expose an unauthenticated DB reader to the internet,"
not "protect secrets."

Concretely, "LAN-only" means:

- **Bind decision.** The uvicorn process binds `0.0.0.0` *inside the
  container* (so the service is reachable across `mission_control_net` and by
  the cockpit frontend). Exposure is constrained at the **Docker publish
  layer**, not by binding to loopback: the host port is published to the
  Beelink's **LAN IP only** (`<beelink-lan-ip>:8848:8848`), never `0.0.0.0` on
  the host. So the API answers on the home network and to co-networked
  containers, and is not listening on any internet-facing interface.
- **The `cloudflared` tunnel does NOT route to the cockpit in v1.**
  `cloudflared` already runs on the Beelink (see `docs/infrastructure.md` →
  "Network reachability"), but **no ingress rule points at `genkei-api`.** The
  read API is deliberately kept off the tunnel so an unauthenticated reader is
  never reachable from the public internet. Adding a tunnel route is a future
  decision that must ship *with* an auth story (token / mTLS / SSO), tracked
  separately if the cockpit ever needs off-LAN access.
- **No auth in v1.** Because the surface is LAN-only and read-only, there are
  no API keys or sessions. This is recorded here so a future "expose it
  remotely" change is forced to revisit auth first.

## Resource-protection defaults

The API shares `genkeicapital-postgres` with every ingest workload, so it is
sized to **never starve ingest**. The defaults mirror `genkei query`'s enforced
limits (B-045) rather than reinventing them — the same read-only guard is the
shared `genkei.common.db.run_readonly` helper both call sites use.

| Guard | Value | Where enforced |
|---|---|---|
| **Connection-pool ceiling** | `max_size=4` (env `GENKEI_API_MAX_POOL_SIZE`) | `src/genkei/api/pool.py` — configured on startup so the shared `db` pool is capped before the first request opens a connection. A burst of cockpit requests can hold at most 4 of the Postgres server's connections. |
| **Read-only transaction** | `SET TRANSACTION READ ONLY` | `db.run_readonly` — Postgres rejects any write; the `/health` probe routes through it, and every reused CLI reader issues plain `SELECT`s. No write helper (`bulk_upsert` / `ingest_run` / `store_raw_blob`) is importable from `genkei.api`. |
| **Statement timeout** | `SET LOCAL statement_timeout` (30 s default; 5 s on `/health`) | `db.run_readonly` — the server cancels a runaway query so it can't pin a pool slot. |
| **Response row cap** | default 100, hard ceiling 1000 rows per list endpoint | `src/genkei/api/app.py` (`DEFAULT_ROW_LIMIT` / `MAX_ROW_LIMIT`) — `/prices` and `/signals` clamp `?limit=` and push it into the SQL `LIMIT`; `/watchlist` and `/research/decisions` are naturally bounded. |

## Endpoints (all read-only)

| Method + path | Returns |
|---|---|
| `GET /health` | Service up + DB reachable (`SELECT 1` under the read-only guard). |
| `GET /watchlist` | The crypto / equity / macro / price watchlist (`?sleeve=` filter). |
| `GET /prices/{ticker}` | Price series (`?source=coingecko\|coinbase\|yahoo`, `?since=`, `?until=`, `?limit=`). |
| `GET /signals` | Signal-event history from `meta.signal_events` (`?asset=`, `?direction=`, date + limit filters). |
| `GET /digest/weekly` | The latest weekly signal-digest markdown from `reports/signals/`. |
| `GET /research/decisions` | Frontmatter index of `docs/research/decisions/*.md`. |
| `GET /lake/health` | Per-source ingest health + primary-table liveness (`?stale_hours=`). |

## Health check + failure alerting (B-119 path)

A dead cockpit service must be noticed like a dead ingester. The
`GET /health` endpoint (service up + DB reachable) is polled by
`.github/workflows/api-health-check.yml`, modeled on
`backup-staleness-check.yml`:

- Runs on the self-hosted `[self-hosted, beelink]` runner (the only place that
  can reach `genkei-api` over `mission_control_net`), on a schedule +
  `workflow_dispatch`.
- Hits `http://genkei-api:8848/health`; a non-200, an unreachable service, or a
  `status != "ok"` body opens/updates a single durable GitHub issue and pings
  Discord via the shared `./.github/actions/discord-notify` action — the exact
  B-119 channel the backup and ingest checks use.
- Registered in `workflow-failure-alert.yml`'s watch list (as
  `"API Health Check"`) so the workflow *itself* failing/timing out is also
  surfaced, matching how the backup check was wired.

## Environment variables

Added to `.env.example`:

- `GENKEI_API_HOST` — uvicorn bind host (default `0.0.0.0`; LAN-only is
  enforced at the publish layer, not by this bind).
- `GENKEI_API_PORT` — uvicorn port (default `8848`).
- `GENKEI_API_MAX_POOL_SIZE` — pool ceiling (default `4`).

`GENKEI_DATABASE_URL` is the same connection string every other component uses
(see `docs/infrastructure.md`).
