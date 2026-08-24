"""FastAPI read layer over the lake (B-131) — the generic HTTP read surface.

Exposes a small set of **read-only** HTTP endpoints (price series, watchlist,
signal history, the weekly signal digest, the research decision log, and lake
health) so revived dashboards or other non-MCP clients have a typed data source
that isn't a Bash-shelled CLI. This is the HTTP sibling of the MCP server
(B-130): same lake, same shared query modules, different transport.

Direct-DB vs subprocess — the recorded decision (B-131)
=======================================================
**The API queries Postgres directly through the shared query modules
(``genkei.common.db`` + the ``genkei.cli.*`` readers), it does NOT shell the
``genkei`` CLI as a subprocess.** This is the *opposite* of the MCP server's
choice (B-130), and deliberately so:

* **Long-lived service, per-request latency matters.** The MCP server is an
  interactive tool surface where a subprocess spawn per call is negligible.
  A web API serving interactive clients answers many small requests; a
  Python-interpreter spawn + Typer bootstrap per request is pure overhead with
  no upside.
* **Reuse, not re-implement.** The endpoints call the *same* functions the
  CLI subcommands call — ``load_watchlist()``, ``_query_source_health()``,
  ``query_events()``, the coingecko/coinbase/yahoo price readers,
  ``build_weekly_digest()``. There is one data-logic layer; the CLI and the
  API are two thin presenters over it. No SQL is duplicated here.
* **Read-only posture is enforced in the engine, not the transport.** Every
  DB-backed endpoint routes through :func:`genkei.common.db.readonly_connection`
  or its single-statement sibling :func:`genkei.common.db.run_readonly` — the
  same ``SET TRANSACTION READ ONLY`` + ``SET LOCAL statement_timeout`` guard
  ``genkei query`` uses (B-045). The API never imports ``bulk_upsert`` /
  ``ingest_run`` / ``store_raw_blob``; it cannot reach a write path. Every list
  endpoint caps its row count server-side.

Resource protection (B-137)
===========================
The API shares one **small** connection pool so it can never starve the ingest
workloads that share ``genkeicapital-postgres``. :func:`configure_pool` sizes
``genkei.common.db``'s process-wide pool with a low ceiling (default 4) on
startup; per-request statement timeouts (default 30 s) and per-endpoint row
caps bound any single query. See ``docs/api-deployment.md`` for the full
deployment + exposure posture (LAN-only, no cloudflared route in v1).

Package layout
==============
* ``serialize`` — a ``JSONResponse`` subclass wired to the shared
  ``genkei.cli._helpers.json_default`` (Decimal→str, dates→ISO) so API JSON
  matches the CLI/MCP shapes byte-for-byte. SDK-free-ish (only fastapi).
* ``pool`` — the connection-pool ceiling + a ``lifespan`` that configures it.
* ``app`` — ``create_app()`` builds the FastAPI app and mounts the endpoints.
  FastAPI is imported lazily inside these modules' functions where practical
  so the package name resolves under the 3.9 harness without the extra.
* ``server`` — the uvicorn entry point (``genkei-api``).

Install with the extra: ``pip install -e ".[api]"`` (FastAPI supports 3.9, so
the endpoint tests run in CI and skip cleanly when the extra is absent).
"""

from __future__ import annotations

__all__ = ["create_app", "main"]


def create_app():  # noqa: ANN201 — return type is fastapi.FastAPI, imported lazily
    """Return the configured FastAPI app. Thin re-export of ``app.create_app``."""
    from genkei.api.app import create_app as _create_app

    return _create_app()


def main() -> None:
    """Console entry point (``genkei-api``). Thin re-export of ``server.main``."""
    from genkei.api.server import main as _main

    _main()
