"""Lua script manager and loader supporting atomic EVALSHA caching."""

from pathlib import Path
from typing import Dict, List, Any
import redis.asyncio as aioredis


SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


class LuaScriptManager:
    """Manages loading and caching of Redis Lua scripts using EVALSHA.

    Instead of sending raw script text over the network on every evaluation,
    Redis pre-compiles scripts via `SCRIPT LOAD` and returns a SHA1 digest.
    Subsequent calls use `EVALSHA <sha1> ...`.
    If Redis restarts or flushes its script cache (raising NOSCRIPT error),
    this manager catches it, re-loads the script, and retries seamlessly.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self.redis = redis_client
        self._shas: Dict[str, str] = {}
        self._sources: Dict[str, str] = {}

    def _read_script(self, script_name: str) -> str:
        """Read Lua script file from disk."""
        if script_name not in self._sources:
            file_path = SCRIPTS_DIR / f"{script_name}.lua"
            if not file_path.exists():
                raise FileNotFoundError(f"Lua script '{script_name}' not found at {file_path}")
            self._sources[script_name] = file_path.read_text(encoding="utf-8")
        return self._sources[script_name]

    async def load_script(self, script_name: str) -> str:
        """Register script with Redis and return its SHA digest."""
        if script_name not in self._shas:
            code = self._read_script(script_name)
            sha = await self.redis.script_load(code)
            self._shas[script_name] = sha
        return self._shas[script_name]

    async def execute(
        self,
        script_name: str,
        keys: List[str],
        args: List[Any],
    ) -> Any:
        """Execute Lua script atomically by SHA, with automatic fallback on NOSCRIPT."""
        sha = await self.load_script(script_name)
        try:
            return await self.redis.evalsha(sha, len(keys), *keys, *args)
        except aioredis.ResponseError as exc:
            # Handle Redis cache eviction / server restart: NOSCRIPT No matching script
            if "NOSCRIPT" in str(exc):
                code = self._read_script(script_name)
                new_sha = await self.redis.script_load(code)
                self._shas[script_name] = new_sha
                return await self.redis.evalsha(new_sha, len(keys), *keys, *args)
            raise
