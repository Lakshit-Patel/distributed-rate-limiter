# Distributed Rate Limiter

A distributed rate-limiting service built with **Python, FastAPI, Redis, and Lua scripts**. It implements four rate-limiting algorithms and uses atomic Redis operations so that multiple API instances enforce one shared request quota.

The project covers concurrency-safe shared state, API middleware, multi-instance Docker deployment, automated testing, and load testing.

## Key Highlights

- **4 algorithms:** Fixed Window, Sliding Window Log, Sliding Window Counter, Token Bucket, behind a common abstract base class.
- **Atomic enforcement:** each check runs as a Redis Lua script (`EVALSHA`), removing GET-then-SET race conditions.
- **Redis server time:** scripts use Redis `TIME`, so enforcement does not depend on application clocks.
- **Distributed:** 3 FastAPI instances behind Nginx share quotas through one Redis.
- **Concurrency tests:** 50 parallel requests against a limit of 10 allow exactly 10; a naive GET/SET limiter leaks past the limit under the same pattern.
- **Load test:** 21,205 requests over 60 seconds with 50 concurrent users, 0 failures (see [Benchmark Results](#benchmark-results) for exactly what that means).
- **CI:** GitHub Actions runs the pytest suite on every push and pull request.

## Architecture

```text
                  Client Requests
                         |
                         v
                 Nginx Reverse Proxy
                    (port 8080)
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
      FastAPI 1      FastAPI 2      FastAPI 3
          |              |              |
          +--------------+--------------+
                         |
                         v
                  Redis 7 (shared state)
                  atomic Lua scripts
```

Whichever API instance receives a request, the same client is checked against the same Redis counters, so a client cannot get extra quota by being routed to a different instance.

## Rate-Limiting Algorithms

| Algorithm | How it works | Trade-off |
|---|---|---|
| Fixed Window | Counts requests per fixed time interval | Simple and cheap; allows bursts at window boundaries |
| Sliding Window Log | Stores a timestamp per request and drops expired ones | Exact enforcement; highest Redis memory use |
| Sliding Window Counter | Weights the previous and current window counters | Good accuracy at low memory cost; an estimate, not exact |
| Token Bucket | Requests spend tokens that refill at a set rate | Allows controlled bursts while limiting sustained traffic |

All four implement `BaseRateLimiter` and are interchangeable in the middleware and route dependencies.

## Demo Endpoints

| Endpoint | Policy |
|---|---|
| `GET /health` | Health check; excluded from rate limiting |
| `GET /api/v1/resource` | Global middleware, Sliding Window Counter: 10 requests / 10 s |
| `GET /api/v1/strict-tier` | Sliding Window Log: 3 requests / 5 s |
| `GET /api/v1/burst-tier` | Token Bucket: capacity 20, refills over 10 s (2 tokens/s) |
| `GET /api/v1/demo-cluster` | Passes through the global middleware; response shows which instance served it |

Rate-limit responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, and `Retry-After` on HTTP 429.

## Features

- FastAPI middleware (global) and dependency-based (per-route) limits.
- Client keys by IP, API key, or user ID, with custom key functions.
- Trusted-proxy validation: `X-Forwarded-For` is only honored when the direct peer is a trusted proxy, so spoofed headers cannot evade limits.
- Configurable `fail_open` / `fail_closed` behavior when Redis is unavailable.
- Lua script SHA caching with automatic recovery from `NOSCRIPT` errors.
- Per-path exclusions for the middleware.

## Configuration

Environment variables read by the demo app (`rate_limiter/app.py`):

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `INSTANCE_ID` | container hostname | Label returned in responses |
| `RATE_LIMIT_FAIL_POLICY` | `fail_open` | `fail_open` or `fail_closed` when Redis is down |
| `TRUSTED_PROXIES` | `127.0.0.1,::1,nginx` | Peers allowed to set forwarded-IP headers |

## Running Locally

### Prerequisites

- Python 3.11+
- Docker Desktop (for the full 3-instance deployment)
- A `redis-server` binary on your PATH (needed only to run the test suite; see Testing)

### Setup

```bash
git clone https://github.com/Lakshit-Patel/distributed-rate-limiter.git
cd distributed-rate-limiter
python -m venv .venv
source .venv/bin/activate        # Windows Git Bash: source .venv/Scripts/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

### Start the deployment

```bash
docker compose -f docker/docker-compose.yml up --build -d
curl http://localhost:8080/health
```

Stop it with:

```bash
docker compose -f docker/docker-compose.yml down
```

## Testing

The suite runs against a **real Redis**, not a mock. `tests/conftest.py` starts a temporary `redis-server` on port 6389 for the session, flushes it between tests, and shuts it down afterward. It uses `tests/bin/redis-server.exe` if present (Windows) and otherwise falls back to a `redis-server` on your PATH.

```bash
python -m pytest -v
python -m pytest --cov=rate_limiter --cov-report=term-missing -v
```

### Latest results

| Metric | Result |
|---|---:|
| Tests passed | 23 / 23 |
| Overall coverage | 98% |

Run on Python 3.12, pytest 9.1.1, Windows. The uncovered lines are a few defensive branches in `middleware.py` (46-49), `base.py` (45) and `naive.py` (51).

### What is covered

| File | Covers |
|---|---|
| `test_algorithms.py` | Fixed window rollover, sliding log enforcement, sliding counter weighting, token bucket refill and burst |
| `test_concurrency.py` | Atomicity under concurrent load (details below) |
| `test_manager.py` | Lua script loading, SHA caching, `NOSCRIPT` recovery, error propagation |
| `test_middleware.py` | Enforcement, excluded paths, trusted-proxy and spoofed-IP handling, custom keys, fail-open / fail-closed |
| `test_dependencies.py` | Per-route dependencies and response headers |
| `test_app.py` | End-to-end API behavior and rate-limit responses |

### Concurrency tests

All three tests use `asyncio.gather` to fire requests simultaneously at one Redis:

| Test | Requests | Limit | Assertion |
|---|---:|---:|---|
| Sliding Window Counter | 50 | 10 | exactly 10 allowed, 40 denied |
| Token Bucket | 60 | 15 | exactly 15 allowed |
| Naive GET-then-SET (control) | 25 | 5 | more than 5 allowed (the race condition) |

The naive limiter is included on purpose: it shows why the atomic Lua scripts are necessary. Note that these tests run in a single process against one Redis, so they validate atomicity at the Redis layer. The multi-instance behavior is exercised by the Docker deployment and the load test below.

### Continuous integration

`.github/workflows/test.yml` runs the pytest suite on every push and pull request to `main`.

## Benchmark Results

A 60-second Locust run against the Docker Compose deployment (3 API instances, Nginx, Redis 7).

### Configuration

| Parameter | Value |
|---|---|
| Tool | Locust 2.46.7 |
| Concurrent users | 50 |
| Spawn rate | 10 users/s |
| Duration | 60 s |
| Target | `http://localhost:8080` |
| Traffic mix | `/resource` ×3, `/strict-tier` ×2, `/burst-tier` ×2, `/demo-cluster` ×1 |

### Results

| Metric | Result |
|---|---:|
| Total requests | 21,205 |
| Failed requests | 0 |
| Average throughput | 362.78 req/s |
| Average response time | 58 ms |
| Median (p50) | 32 ms |
| p95 | 180 ms |
| p99 | 410 ms |
| Maximum | 874 ms |

### How to read these numbers

- **Throughput is rate-limit decisions per second, not served traffic.** The demo limits are small (10 / 10 s, 3 / 5 s, 20 / 10 s), so under 50 concurrent users most responses are HTTP 429 rejections.
- **"0 failures" means no unexpected responses.** The locustfile counts
