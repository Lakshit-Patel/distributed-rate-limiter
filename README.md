# Distributed Rate Limiter in Python

A distributed API rate-limiting system built with **Python, FastAPI, Redis, and atomic Lua scripts**. It demonstrates how multiple API instances can enforce shared request quotas using Redis as a centralized state store.

The project includes four rate-limiting algorithms, asynchronous Redis operations, concurrency testing, trusted-proxy validation, configurable Redis failure policies, and a Docker-based multi-instance deployment.

---

## 1. Problem Statement

In distributed applications, multiple API servers may receive requests from the same client simultaneously. If each server maintains its own local request counter, clients can bypass rate limits simply by reaching different instances.

A centralized rate limiter solves this by maintaining shared quota state in Redis.

### Problems addressed

**Race conditions in naive implementations**

A conventional Redis GET-then-SET sequence can produce race conditions under concurrency. Multiple requests may read the same counter before any of them updates it, allowing more requests than the configured limit.

**Inconsistent application clocks**

Different application instances may have slightly different system clocks. Using their local timestamps for distributed rate limiting can produce inconsistent window calculations.

**Single-instance quota enforcement**

Independent API servers must coordinate their quota decisions to enforce a consistent global limit.

### Project approach

- Uses Redis Lua scripts for atomic execution of production rate-limiting algorithms.
- Uses Redis server time (`TIME`) for time calculations.
- Shares rate-limit state across multiple FastAPI instances.
- Includes a naive GET/SET implementation to demonstrate concurrency-related leakage.
- Supports configurable failure behavior when Redis is unavailable.

---

## 2. System Architecture

```text
                 Incoming Client Requests
                           |
                           v
                  Nginx Reverse Proxy
                     Port 8080
                           |
             Round-Robin Load Balancing
                           |
          +----------------+----------------+
          |                |                |
          v                v                v
     FastAPI App 1    FastAPI App 2    FastAPI App 3
       api-1             api-2             api-3
          |                |                |
          +----------------+----------------+
                           |
                  Rate Limiting Middleware
                           |
                  Async Redis Connection
                           |
                  Atomic Lua Execution
                       EVALSHA
                           |
                           v
                     Redis 7.x
                  Shared Quota State
```

### Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| API framework | FastAPI |
| Data store | Redis 7 |
| Redis client | redis-py asyncio |
| Atomic execution | Redis Lua scripts |
| HTTP server | Uvicorn |
| Reverse proxy | Nginx |
| Containerization | Docker and Docker Compose |
| Testing | Pytest, pytest-asyncio |
| HTTP testing | HTTPX |
| Load testing | Locust |

---

## 3. Rate-Limiting Algorithms

The project implements four rate-limiting algorithms.

| Algorithm | Redis Data Structure | Time Complexity | Space Complexity | Behavior |
|---|---|---|---|---|
| Fixed Window Counter | String | O(1) | O(1) | Counts requests within fixed time intervals |
| Sliding Window Log | Sorted Set | O(log N + M) | O(N) | Tracks individual request timestamps |
| Sliding Window Counter | Hash | O(1) | O(1) | Uses weighted previous-window counts |
| Token Bucket | Hash | O(1) | O(1) | Allows controlled bursts with continuous refill |

### Fixed Window Counter

Maintains a request counter for a fixed time interval.

- Simple implementation.
- Low memory overhead.
- Can permit traffic spikes around window boundaries.

### Sliding Window Log

Stores individual request timestamps in a Redis sorted set.

- Removes expired timestamps.
- Counts requests inside the active rolling window.
- Provides precise rolling-window enforcement.
- Uses memory proportional to the number of tracked requests.

### Sliding Window Counter

Uses the current and previous window counts to estimate the number of requests in a rolling interval.

Weighted count:

```text
Estimated Count =
Current Window Count +
Previous Window Count × (1 - Elapsed Ratio)
```

This provides a memory-efficient approximation of a sliding window.

### Token Bucket

Maintains a token balance that refills over time.

- Supports controlled request bursts.
- Refills tokens continuously.
- Rejects requests when insufficient tokens remain.
- Uses Redis server time for refill calculations.

The token bucket implementation uses Redis Lua scripts to perform refill and token deduction atomically.

---

## 4. Key Engineering Features

### Atomic Redis Operations

Production rate-limiting algorithms use Lua scripts executed through Redis `EVALSHA`.

Scripts are loaded using `SCRIPT LOAD` and cached by their SHA digest.

The script manager also handles `NOSCRIPT` errors by reloading and retrying the relevant script.

A separate naive GET/SET implementation is included to demonstrate race conditions.

### Distributed Quota Enforcement

All three FastAPI instances use the same Redis-backed quota state.

This means a client's requests are counted against a shared quota regardless of which API instance handles the request.

### Trusted Proxy Validation

Client IP extraction supports:

- `X-Forwarded-For`
- `CF-Connecting-IP`
- Trusted proxy IP addresses
- Trusted proxy CIDR ranges

Forwarded IP headers are only accepted when the immediate socket peer is trusted.

This prevents untrusted clients from manipulating their rate-limit identity through spoofed headers.

### Configurable Redis Failure Policies

Two failure policies are supported:

**FAIL_OPEN**

Allows requests to continue when Redis is unavailable. Responses include:

```text
X-RateLimit-Degraded: true
```

**FAIL_CLOSED**

Rejects requests when the rate-limiting service is unavailable.

This prioritizes backend protection over availability.

### Rate-Limit Response Headers

Responses include standard project headers:

```text
X-RateLimit-Limit
X-RateLimit-Remaining
X-RateLimit-Reset
Retry-After
```

Rejected requests receive HTTP `429 Too Many Requests`.

---

## 5. API Endpoints

| Endpoint | Algorithm / Purpose | Configuration |
|---|---|---|
| `/health` | Health check | No rate limiting |
| `/api/v1/resource` | Sliding Window Counter middleware | 10 requests / 10 seconds |
| `/api/v1/strict-tier` | Sliding Window Log | 3 requests / 5 seconds |
| `/api/v1/burst-tier` | Token Bucket | Capacity 20, refill 2 tokens/second |
| `/api/v1/demo-cluster` | Distributed cluster demonstration | Shared middleware quota |

### Example Requests

Check application health:

```bash
curl http://localhost:8080/health
```

Test the shared resource quota:

```bash
curl -i \
  -H "X-User-ID: user-123" \
  http://localhost:8080/api/v1/resource
```

Test the strict sliding-window endpoint:

```bash
curl -i \
  -H "X-User-ID: user-123" \
  http://localhost:8080/api/v1/strict-tier
```

Test the token bucket:

```bash
curl -i \
  -H "X-User-ID: user-123" \
  http://localhost:8080/api/v1/burst-tier
```

---

## 6. Docker Deployment

The Docker Compose configuration launches:

- One Redis 7 instance.
- Three FastAPI application instances.
- One Nginx reverse proxy.

### Start the cluster

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

### Check container status

```bash
docker compose -f docker/docker-compose.yml ps
```

### Test load balancing

```bash
for i in {1..9}; do
  curl -s http://localhost:8080/api/v1/demo-cluster
  echo
done
```

Repeated requests demonstrate Nginx round-robin distribution across the three API instances.

### Verified deployment behavior

The following behaviors have been tested:

- All five containers build and start.
- Redis reports healthy status.
- Nginx distributes requests across three FastAPI instances.
- Multiple API instances share the same Redis-backed rate-limit quota.
- The global resource endpoint allows 10 requests and rejects subsequent requests within the configured window.
- The strict sliding-window endpoint enforces its configured limit.
- The token bucket endpoint enforces its configured capacity and refill behavior.

---

## 7. Testing and Code Coverage

The project uses Pytest and a real Redis test server for algorithm and integration testing.

Run the complete test suite:

```bash
python -m pytest -v
```

Run tests with coverage:

```bash
python -m pytest \
  --cov=rate_limiter \
  --cov-report=term-missing \
  -v
```

### Verified Test Results

Latest verified local test run:

| Metric | Result |
|---|---:|
| Total tests | 23 |
| Passed | 23 |
| Failed | 0 |
| Overall code coverage | 98% |
| Application coverage | 100% |
| Redis script manager coverage | 100% |
| Route dependency coverage | 100% |
| Middleware coverage | 96% |

### Test Coverage Includes

- Fixed window boundaries and rollover.
- Sliding window log enforcement.
- Sliding window counter calculations.
- Token bucket refill and burst behavior.
- Concurrent Redis operations.
- Naive GET/SET race-condition demonstration.
- Lua script caching and `NOSCRIPT` recovery.
- API route integration and rate-limit responses.
- Trusted proxy and spoofed IP handling.
- Custom rate-limit keys.
- Redis failure policies.
- Excluded middleware paths.

Coverage and test results reflect the local test run and are not a substitute for production load testing.

---

## 8. Benchmarking

Performance benchmarking with Locust has not yet been completed.

No throughput, latency, or production capacity figures are claimed.

The following command can be used to run a Locust test against the Docker deployment:

```bash
locust -f tests/locustfile.py \
  --headless \
  -u 50 \
  -r 10 \
  --run-time 1m \
  --host http://localhost:8080
```

Record actual measurements for:

- Requests per second.
- Median latency (p50).
- p95 latency.
- p99 latency.
- HTTP error rate.
- Rate-limit leakage under concurrency.

---

## 9. Known Limitations and Future Improvements

### Redis High Availability

The current Docker deployment uses a single Redis instance, which is a potential single point of failure.

Future improvement: Redis Sentinel or Redis Cluster.

### Observability

The project currently focuses on rate-limiting behavior rather than a complete production monitoring stack.

Future improvement: Prometheus metrics, Grafana dashboards, structured logs, and distributed tracing.

### Dynamic Tenant Quotas

Current quotas are configured through routes and application settings.

Future improvement: Dynamic per-tenant quotas with a cached configuration store.

### Performance Optimization

Each rate-limited request communicates with Redis.

Future improvement: Evaluate carefully designed local caching or quota allocation strategies, while preserving global enforcement guarantees.

---

## 10. Project Structure

```text
distributed-rate-limiter/
│
├── rate_limiter/
│   ├── algorithms/
│   │   ├── fixed_window.py
│   │   ├── sliding_window_log.py
│   │   ├── sliding_window_counter.py
│   │   ├── token_bucket.py
│   │   └── naive.py
│   │
│   ├── middleware/
│   │   ├── middleware.py
│   │   └── depends.py
│   │
│   ├── app.py
│   ├── base.py
│   └── manager.py
│
├── docker/
│   ├── docker-compose.yml
│   └── nginx.conf
│
├── tests/
│   ├── test_algorithms.py
│   ├── test_app.py
│   ├── test_concurrency.py
│   ├── test_dependencies.py
│   ├── test_manager.py
│   ├── test_middleware.py
│   └── locustfile.py
│
├── docs/
│   └── INTERVIEW_NOTES.md
│
├── scripts/
├── pyproject.toml
└── README.md
```

---

## 11. Running Locally

Clone the repository:

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd distributed-rate-limiter
```

Install dependencies:

```bash
pip install -e ".[dev]"
```

Start Redis locally or through Docker.

Run the application:

```bash
uvicorn rate_limiter.app:app --reload
```

Run tests:

```bash
python -m pytest -v
```

---

## 12. Learning Outcomes

This project demonstrates practical understanding of:

- Distributed systems and shared state.
- Redis data structures and Lua scripting.
- Atomic operations and concurrency control.
- Rate-limiting algorithm trade-offs.
- Asynchronous Python application development.
- API middleware and request identity.
- Reverse proxies and load balancing.
- Docker-based multi-instance deployment.
- Failure handling and automated testing.

For detailed technical explanations and interview preparation, see [docs/INTERVIEW_NOTES.md](docs/INTERVIEW_NOTES.md).
