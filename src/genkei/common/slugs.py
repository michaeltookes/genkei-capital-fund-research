"""Small shared slug helpers for stable data-lake identifiers."""

from __future__ import annotations


def blob_slug_part(value: str) -> str:
    """Normalize route, endpoint, or field text for raw blob endpoint names."""
    return value.strip("/").strip().replace("/", "_").replace(" ", "_").lower()
