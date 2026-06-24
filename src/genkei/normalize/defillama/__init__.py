"""DeFiLlama normalizer package (B-121 split of the former single module).

Public API is unchanged: ``from genkei.normalize.defillama import normalize``,
``normalize_protocols``, etc. all still resolve here, and
``python -m genkei.normalize.defillama`` runs via ``__main__``. The module was
split into ``core`` (the normalizers + run orchestration) and ``dispatch`` (the
raw-blob prefix routing table) once it crossed ~950 lines.
"""

from __future__ import annotations

from genkei.normalize.defillama.core import *  # noqa: F401,F403
from genkei.normalize.defillama.core import (  # noqa: F401
    _rows_for,
    _stablecoin_supply,
    _upsert_protocol_fee_rows,
    db,
)
from genkei.normalize.defillama.dispatch import (  # noqa: F401
    BLOB_ROUTES,
    CHAIN_HISTORY_PREFIX,
    PRICE_HISTORICAL_PREFIX,
    PROTOCOL_FEES_PREFIX,
    PROTOCOL_HISTORY_PREFIX,
    PROTOCOL_REVENUE_PREFIX,
    STABLECOIN_HISTORY_PREFIX,
    PrefixRoute,
    classify_blob,
)
