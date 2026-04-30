"""Unit tests for CitingDescriptionCache."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import asyncio
import json
import pytest
from citationclaw.core.citing_description_cache import CitingDescriptionCache


def run(coro):
    return asyncio.run(coro)


def test_miss_returns_none(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    result = cache.get("http://example.com/p1", "Paper A", "Target Paper")
    assert result is None


def test_update_then_get(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    run(cache.update("http://example.com/p1", "Paper A", "Target Paper", "Great work"))
    result = cache.get("http://example.com/p1", "Paper A", "Target Paper")
    assert result == "Great work"


def test_fallback_to_title_key(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    run(cache.update("", "Paper A", "Target Paper", "Cited in intro"))
    result = cache.get("", "Paper A", "Target Paper")
    assert result == "Cited in intro"


def test_title_key_is_lowercase(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    run(cache.update("", "Paper A", "Target Paper", "Desc"))
    # same title different case should hit
    result = cache.get("", "paper a", "target paper")
    assert result == "Desc"


def test_different_target_papers_different_keys(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    run(cache.update("http://p1", "Paper A", "Target X", "Desc X"))
    run(cache.update("http://p1", "Paper A", "Target Y", "Desc Y"))
    assert cache.get("http://p1", "Paper A", "Target X") == "Desc X"
    assert cache.get("http://p1", "Paper A", "Target Y") == "Desc Y"


def test_persists_to_disk(tmp_path):
    f = tmp_path / "cache.json"
    cache1 = CitingDescriptionCache(cache_file=f)
    run(cache1.update("http://p1", "Paper A", "Target", "Saved desc"))
    run(cache1.flush())

    cache2 = CitingDescriptionCache(cache_file=f)
    assert cache2.get("http://p1", "Paper A", "Target") == "Saved desc"


def test_has_description_true(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    run(cache.update("http://p1", "Paper A", "Target", "Some desc"))
    assert cache.has_description("http://p1", "Paper A", "Target") is True


def test_has_description_false(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    assert cache.has_description("http://p1", "Paper A", "Target") is False


def test_stats(tmp_path):
    cache = CitingDescriptionCache(cache_file=tmp_path / "cache.json")
    run(cache.update("http://p1", "Paper A", "Target", "Desc"))
    cache.get("http://p1", "Paper A", "Target")   # hit
    cache.get("http://p2", "Paper B", "Target")   # miss
    s = cache.stats()
    assert s["total_entries"] == 1
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["updates"] == 1
