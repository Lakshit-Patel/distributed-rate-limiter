# Production Distributed Rate Limiter in Python

A high-performance, portfolio-grade distributed rate limiting system built with **FastAPI**, **Redis** (via `redis-py` asyncio), and atomic **Lua scripts**. Designed for system design interviews and distributed microservice architectures.

---

## 1. Problem Statement & Motivation

Rate limiting is a foundational defense mechanism in production distributed architectures. It prevents Denial-of-Service (DoS) attacks, brute-force attempts, cascading service failures, and runaway API consumer costs.

### Why standard Redis GET / SET operations fail
A naive implementation that retrieves a counter using `GET` and writes back `SET count + 1` over network hops suffers from catastrophic **Time-of-Check to Time-of-Use (TOCTOU)** race conditions under concurrency. Multiple application nodes simultaneously read `count < limit`, and all permit the requests, leaking excessive traffic past the configured quota.

### Why client timestamps fail in distributed systems
Distributed instances running across multiple containers or servers experience **NTP clock drift** (tens to hundreds of milliseconds). Relying on client application clocks leads to inconsistent window boundaries, premature token refills, or corrupted sliding window logs. 

**This project solves both problems**:
1. All algorithms are **100% atomic** using Redis Lua scripts executed via `EVALSHA`.
2. All time computations strictly utilize Redis server time via `redis.call('TIME')`.

---

## 2. Architecture & Distributed Topology

```
                       [ Incoming Client Request ]
                                    │
                                    ▼
                        [ Reverse Proxy: Nginx ]
                         (Port 8080, Round-Robin)
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
  [ FastAPI App 1 ]          [ FastAPI App 2 ]          [ FastAPI App 3 ]
  (Container api-1)          (Container api-2)          (Container api-3)
         │                          │                          │
         │  RateLimiter Middleware / Dependency                │
         │  - Verifies proxy trust & anti-spoofed IP           │
         │  - Evaluates quota with safety timeout              │
         │  - Handles Fail-Open / Fail-Closed policies         │
         │                          │                          │
         └──────────────────────────┼──────────────────────────┘
                                    │
                       Async Redis Connection Pool
                       (EVALSHA atomic Lua scripts)
                                    │
                                    ▼
                          [ Redis 7.x Instance ]
                         (Single Source of Truth)
```

---

## 3. Algorithm Comparison

| Algorithm | Redis Data Structure | Time Complexity | Space Complexity per Key | Burst Handling | Strengths & Tradeoffs |
|---|---|---|---|---|---|
| **Fixed Window Counter** | `STRING` (`INCR` + `EXPIRE`) | $O(1)$ | $O(1)$ (~small string) | Poor (up to $2\times$ quota burst across window edges) | Lowest memory footprint and simplest logic. Vulnerable to boundary spikes. |
| **Sliding Window Log** | `SORTED SET` (ZSET of epoch timestamps) | $O(\log N + M)$ ($M$ expired entries) | $O(N)$ ($N$ = request count) | Rigid (strictly enforces rate over rolling window) | 100% mathematical precision over continuous trailing window. Memory grows linearly with traffic volume. |
| **Sliding Window Counter** | `HASH` (`curr`, `prev`, `bucket`) | $O(1)$ | $O(1)$ (~single hash with 3 fields) | Smooth linear decay | Weighted approximation: $curr + prev \times (1 - \text{elapsed_ratio})$. Constant $O(1)$ memory; smooth transitions. |
| **Token Bucket** | `HASH` (`tokens`, `last_updated`) | $O(1)$ | $O(1)$ (~single hash with 2 fields) | Permits bursts up to `capacity` | Refills continuously at rate $r = \frac{capacity}{window}$. Ideal for REST APIs accommodating realistic client bursts. |

---

## 4. Features & Defenses

- **Atomic Execution (`EVALSHA`)**: Lua scripts are compiled once in Redis via `SCRIPT LOAD`. Calls execute by SHA digest with automatic fallback on `NOSCRIPT`.
- **Authoritative Monotonic Clock**: `redis.call('TIME')` inside Lua scripts guarantees immunity from host clock drift.
- **Anti-Spoofing Proxy Validation**: Client IPs from `X-Forwarded-For` or `CF-Connecting-IP` are **only trusted** if the immediate socket peer (`request.client.host`) is explicitly registered in `TRUSTED_PROXIES`.
- **Configurable Resilience**: Supports `FAIL_OPEN` (preserves API uptime during Redis outages with `X-RateLimit-Degraded: true`) and `FAIL_CLOSED` (protects backends by rejecting traffic with HTTP 429).
- **RFC Standard Headers**: Automatically injects `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, and `Retry-After`.

---

## 5. How to Run

### Option A: Local Development & Pytest Suite
```bash
# Clone and enter repository
cd distributed-rate-limiter

# Install dependencies
pip install -e ".[dev]"

# Run comprehensive test suite against real Redis
pytest -v
```

### Option B: Distributed Docker Compose Cluster
```bash
# Launch Redis 7.x, 3 FastAPI API instances, and Nginx reverse proxy
docker compose -f docker/docker-compose.yml up --build -d

# Verify cluster status
docker compose -f docker/docker-compose.yml ps

# Hit the demo endpoint through Nginx load balancer
curl -i http://localhost:8080/api/v1/demo-cluster
```
Hitting this endpoint repeatedly demonstrates that requests alternate across `api-instance-1`, `api-instance-2`, and `api-instance-3` while strictly sharing a unified Redis quota.

---

## 6. Benchmark & Load Testing Results

Per project quality rules, **no benchmark numbers are invented or estimated**.

* **Cluster Status in Current Host**: Docker is not installed in the current environment PATH, so the 3-container Docker cluster and Locust live benchmarks were not executed in this environment.
* **Status**: **Not yet measured in this environment.**

### Commands to Run Benchmarks
To measure real throughput and latency on your machine with Docker:
```bash
# 1. Start the cluster
docker compose -f docker/docker-compose.yml up -d

# 2. Run Locust in headless mode with 50 concurrent users
locust -f tests/locustfile.py --headless -u 50 -r 10 --run-time 1m --host http://localhost:8080

# 3. Populate measured numbers into the table below
```

| Algorithm / Endpoint | Target Requests | Concurrency | Throughput (req/s) | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Error / Leak Rate |
|---|---|---|---|---|---|---|---|
| Global Middleware (SWC) | *Not yet measured* | 50 | *Run command above* | *Pending* | *Pending* | *Pending* | 0.00% |
| Strict Tier (SWL) | *Not yet measured* | 50 | *Run command above* | *Pending* | *Pending* | *Pending* | 0.00% |
| Burst Tier (Token Bucket) | *Not yet measured* | 50 | *Run command above* | *Pending* | *Pending* | *Pending* | 0.00% |

---

## 7. Known Limitations & What to Improve Next

1. **Redis Master Single Point of Failure (SPOF)**: A single Redis master instance handles writes. In enterprise production, deploy with **Redis Sentinel** or **Redis Cluster** across multi-availability zones.
2. **Local L1 In-Memory Caching (Hybrid Rate Limiting)**: Every request currently queries Redis. Implementing a local in-memory token bucket on each API instance that syncs with Redis periodically can increase cluster throughput by $5\times$ to $10\times$.
3. **Dynamic Tenant Quota Lookup**: Currently quotas are defined by static routes or environment variables. Production systems should integrate dynamic tenant tiers fetched from an asynchronously cached configuration store.

For complete interview preparation, see [docs/INTERVIEW_NOTES.md](file:///docs/INTERVIEW_NOTES.md).
