"""Tests for Redis Lua script loading, caching, and recovery."""

import pytest
import redis.asyncio as aioredis

from rate_limiter.manager import LuaScriptManager


class FakeRedis:
    def __init__(self):
        self.load_count = 0
        self.eval_count = 0
        self.fail_first_eval = False
        self.error_message = "NOSCRIPT No matching script"

    async def script_load(self, code):
        self.load_count += 1
        return f"fake-sha-{self.load_count}"

    async def evalsha(self, sha, numkeys, *keys_and_args):
        self.eval_count += 1

        if self.fail_first_eval and self.eval_count == 1:
            raise aioredis.ResponseError(self.error_message)

        return "executed"


@pytest.mark.asyncio
async def test_script_loading_is_cached():
    redis = FakeRedis()
    manager = LuaScriptManager(redis)

    first_sha = await manager.load_script("fixed_window")
    second_sha = await manager.load_script("fixed_window")

    assert first_sha == second_sha
    assert redis.load_count == 1


@pytest.mark.asyncio
async def test_execute_recovers_from_noscript_error():
    redis = FakeRedis()
    redis.fail_first_eval = True
    manager = LuaScriptManager(redis)

    result = await manager.execute(
        "fixed_window",
        keys=["test:key"],
        args=[5, 10],
    )

    assert result == "executed"
    assert redis.load_count == 2
    assert redis.eval_count == 2


@pytest.mark.asyncio
async def test_execute_propagates_non_noscript_errors():
    redis = FakeRedis()
    redis.fail_first_eval = True
    redis.error_message = "WRONGTYPE Operation against a key"
    manager = LuaScriptManager(redis)

    with pytest.raises(aioredis.ResponseError, match="WRONGTYPE"):
        await manager.execute(
            "fixed_window",
            keys=["test:key"],
            args=[5, 10],
        )

    assert redis.load_count == 1
    assert redis.eval_count == 1


def test_missing_lua_script_raises_file_not_found():
    manager = LuaScriptManager(FakeRedis())

    with pytest.raises(FileNotFoundError, match="not found"):
        manager._read_script("definitely_missing_script")
