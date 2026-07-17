"""Unit tests for the disk-backed query cache (B-046)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.common import cache


class CacheTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(
            "os.environ", {"GENKEI_CACHE_DIR": self._tmp.name}, clear=False
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        # Clear the TTL env so it doesn't leak from the host.
        self._ttl_env = patch.dict("os.environ", {}, clear=False)
        self._ttl_env.start()
        self.addCleanup(self._ttl_env.stop)


class MakeKeyTests(unittest.TestCase):
    def test_stable_for_same_parts(self) -> None:
        self.assertEqual(
            cache.make_key("query", "SELECT 1", 100, "table"),
            cache.make_key("query", "SELECT 1", 100, "table"),
        )

    def test_differs_on_any_part(self) -> None:
        base = cache.make_key("query", "SELECT 1", 100, "table")
        self.assertNotEqual(base, cache.make_key("query", "SELECT 2", 100, "table"))
        self.assertNotEqual(base, cache.make_key("query", "SELECT 1", 5, "table"))
        self.assertNotEqual(base, cache.make_key("query", "SELECT 1", 100, "json"))

    def test_key_is_hex_filename_safe(self) -> None:
        key = cache.make_key("query", "SELECT 1", 100, "table")
        self.assertTrue(all(c in "0123456789abcdef" for c in key))
        self.assertEqual(len(key), 64)


class StoreLoadTests(CacheTestBase):
    def test_roundtrip(self) -> None:
        cache.store("k1", "hello world", now=1000.0)
        self.assertEqual(cache.load("k1", ttl=300, now=1000.0), "hello world")

    def test_miss_returns_none(self) -> None:
        self.assertIsNone(cache.load("nope", ttl=300))

    def test_expired_entry_returns_none_and_unlinks(self) -> None:
        cache.store("k2", "stale", now=1000.0)
        # 301s later, ttl=300 → expired.
        self.assertIsNone(cache.load("k2", ttl=300, now=1301.0))
        # File removed on the expiring read.
        self.assertFalse((cache.cache_dir() / "k2.json").exists())

    def test_within_ttl_hits(self) -> None:
        cache.store("k3", "fresh", now=1000.0)
        self.assertEqual(cache.load("k3", ttl=300, now=1299.0), "fresh")

    def test_corrupt_file_returns_none(self) -> None:
        (cache.cache_dir() / "bad.json").write_text("not json{", encoding="utf-8")
        self.assertIsNone(cache.load("bad", ttl=300))

    def test_entry_without_value_returns_none(self) -> None:
        (cache.cache_dir() / "partial.json").write_text(
            '{"stored_at": 1000.0}', encoding="utf-8"
        )
        self.assertIsNone(cache.load("partial", ttl=300, now=1000.0))

    def test_store_overwrites(self) -> None:
        cache.store("k4", "v1", now=1000.0)
        cache.store("k4", "v2", now=1001.0)
        self.assertEqual(cache.load("k4", ttl=300, now=1001.0), "v2")

    def test_clear_removes_all(self) -> None:
        cache.store("a", "1", now=1000.0)
        cache.store("b", "2", now=1000.0)
        removed = cache.clear()
        self.assertEqual(removed, 2)
        self.assertIsNone(cache.load("a", ttl=300, now=1000.0))


class CacheDirTests(CacheTestBase):
    def test_honors_env_dir(self) -> None:
        self.assertEqual(cache.cache_dir().parent, Path(self._tmp.name))
        self.assertEqual(cache.cache_dir().name, "query")

    def test_dir_is_created(self) -> None:
        self.assertTrue(cache.cache_dir().is_dir())


class DefaultTtlTests(unittest.TestCase):
    def test_default_when_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("GENKEI_CACHE_TTL", None)
            self.assertEqual(cache.default_ttl(), cache.DEFAULT_TTL_SECONDS)

    def test_env_override(self) -> None:
        with patch.dict("os.environ", {"GENKEI_CACHE_TTL": "45"}, clear=False):
            self.assertEqual(cache.default_ttl(), 45)

    def test_invalid_env_falls_back(self) -> None:
        with patch.dict("os.environ", {"GENKEI_CACHE_TTL": "banana"}, clear=False):
            self.assertEqual(cache.default_ttl(), cache.DEFAULT_TTL_SECONDS)

    def test_nonpositive_env_falls_back(self) -> None:
        with patch.dict("os.environ", {"GENKEI_CACHE_TTL": "0"}, clear=False):
            self.assertEqual(cache.default_ttl(), cache.DEFAULT_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main()
