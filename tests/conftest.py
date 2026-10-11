"""Pytest fixtures providing an ephemeral, real Redis instance and client."""

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator
import pytest
import pytest_asyncio
import redis.asyncio as aioredis


TEST_REDIS_PORT = 6389
TEST_REDIS_URL = f"redis://127.0.0.1:{TEST_REDIS_PORT}/0"


@pytest.fixture(scope="session")
def real_redis_server():
    """Start a real Redis server process for test execution."""
    bin_dir = Path(__file__).resolve().parent / "bin"
    redis_exe = bin_dir / "redis-server.exe"

    if not redis_exe.exists():
        # Fallback to system redis-server if installed
        redis_cmd = "redis-server"
    else:
        redis_cmd = str(redis_exe)

    proc = subprocess.Popen(
        [redis_cmd, "--port", str(TEST_REDIS_PORT), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Redis to start accepting connections
    time.sleep(0.8)

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest_asyncio.fixture
async def redis_client(real_redis_server) -> AsyncGenerator[aioredis.Redis, None]:
    """Async Redis client connected to the real test Redis instance."""
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True, protocol=2)
    # Flush database before every test to ensure strict test isolation
    await client.flushall()
    try:
        yield client
    finally:
        await client.flushall()
        await client.aclose()
