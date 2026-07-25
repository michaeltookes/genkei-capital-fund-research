"""JSON serialization for the read API (B-131).

FastAPI's default encoder does not know how to render ``Decimal`` or the
project's date/datetime shapes the way the CLI does — so responses would
drift from the ``genkei ... --json`` / MCP output. This module wires the
shared :func:`genkei.cli._helpers.json_default` (Decimal→str, dates→ISO) into
a ``JSONResponse`` subclass, so every endpoint returns the exact byte shape a
consumer already gets from the CLI.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse

from genkei.cli._helpers import json_default as _json_default


class GenkeiJSONResponse(JSONResponse):
    """``JSONResponse`` that serializes with the shared ``json_default`` hook.

    Decimal → string (lossless), anything with ``isoformat`` → ISO 8601 —
    identical to ``genkei query --json`` and the MCP tool output, so the
    cockpit sees one canonical JSON contract regardless of transport.
    """

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            default=_json_default,
        ).encode("utf-8")
