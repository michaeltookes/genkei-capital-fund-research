"""uvicorn entry point for the read API (B-131). Console script: ``genkei-api``.

Binds to ``GENKEI_API_HOST`` / ``GENKEI_API_PORT`` (defaults documented in
``.env.example`` and ``docs/api-deployment.md``). In the container the bind is
``0.0.0.0`` so the service is reachable across ``mission_control_net``; the
LAN-only posture is enforced at the Docker/publish layer (no host port
published to the public interface, no cloudflared route), NOT by binding to
loopback — see ``docs/api-deployment.md`` for the full exposure decision.

``uvicorn`` (and its ``[standard]`` deps) ship with the ``[api]`` extra; this
module imports it lazily inside ``main`` so ``genkei.api`` still imports under
the 3.9 harness without the extra installed.
"""

from __future__ import annotations

import os

DEFAULT_HOST = "0.0.0.0"  # noqa: S104 — LAN-only is enforced at publish layer, see module docstring
DEFAULT_PORT = 8848


def _host() -> str:
    raw = os.environ.get("GENKEI_API_HOST")
    return raw.strip() if raw and raw.strip() else DEFAULT_HOST


def _port() -> int:
    raw = os.environ.get("GENKEI_API_PORT")
    if raw and raw.strip():
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_PORT


def main() -> None:
    """Run the read API under uvicorn."""
    import uvicorn

    from genkei.api.app import create_app

    uvicorn.run(create_app(), host=_host(), port=_port())


if __name__ == "__main__":  # pragma: no cover
    main()
