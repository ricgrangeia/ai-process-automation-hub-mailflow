"""
Tests for app/core/operation_mode.py

Uses fakeredis — no real Redis required.
"""
import pytest
import fakeredis.aioredis as fakeredis

from app.core.operation_mode import (
    get_mode, set_mode,
    DEFAULT_MODE, MODES, OPERATION_MODE_KEY,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def r():
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_default_mode_when_key_absent(r):
    assert await get_mode(r) == DEFAULT_MODE


async def test_default_mode_is_hybrid(r):
    assert DEFAULT_MODE == "hybrid"


async def test_set_and_get_each_mode(r):
    for mode in MODES:
        await set_mode(r, mode)
        assert await get_mode(r) == mode


async def test_invalid_mode_raises_value_error(r):
    with pytest.raises(ValueError, match="Unknown mode"):
        await set_mode(r, "turbo_mode")


async def test_garbage_redis_value_falls_back_to_default(r):
    await r.set(OPERATION_MODE_KEY, "not_a_valid_mode")
    assert await get_mode(r) == DEFAULT_MODE


async def test_overwrite_mode(r):
    await set_mode(r, "hybrid")
    await set_mode(r, "auto_learn")
    assert await get_mode(r) == "auto_learn"


async def test_all_modes_defined():
    assert set(MODES.keys()) == {"hybrid", "auto_learn"}


async def test_mode_labels_are_non_empty():
    for key, label in MODES.items():
        assert label, f"Mode '{key}' has an empty label"
