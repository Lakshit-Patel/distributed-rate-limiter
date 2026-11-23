# Distributed Rate Limiter

A distributed rate-limiting service built with **Python, FastAPI, Redis, and Lua scripts**. It supports multiple rate-limiting algorithms and uses atomic Redis operations to enforce shared request quotas across multiple API instances.

The project demonstrates distributed state management, concurrency-safe rate limiting, API middleware, Docker-based deployment, automated testing, and load testing.

## Key Highlights

- **4 rate-limiting algorithms:** Fixed Window, Sliding Window Log, Sliding Window Counter, and Token Bucket.
- **Atomic Redis operations:** Lua scripts prevent race conditions during concurrent requests.
- **Distributed enforcement:** Multiple FastAPI instances share rate-limit state through Redis.
- **3 API instances:** Deployed behind an Nginx reverse proxy using Docker Compose.
- **21,205 requests tested:** Completed a 60-second Locust load test with 50 concurrent users.
- **0% request failures:** No failed requests reported during the recorded benchmark.
- **362.78 requests/second:** Throughput reported by Locust during the benchmark.
- **98% test coverage:** From the project's earlier local coverage run; see the testing section.

## Benchmark Results

A load test was conducted using Locust against the Docker Compose deployment.

### Test configuration

| Parameter | Value |
|---|---|
| Load testing tool | Locust 2.46.7 |
| Concurrent users | 50 |
| User spawn rate | 10 users/second |
| Test duration | 60 seconds |
| API instances | 3 |
| Reverse proxy | Nginx |
| Shared datastore | Redis |
| Target | `http://localhost:8080` |

### Measured results

| Metric | Result |
|---|---:|
| Total requests | 21,205 |
| Failed requests | 0 |
| Failure rate | 0.00% |
| Throughput (Locust reported) | 362.78 req/s |
| Average response time | 58 ms |
| Median response time (p50) | 32 ms |
| 95th percentile (p95) | 180 ms |
| 99th percentile (p99) | 410 ms |
| Maximum response time | 870 ms |

### Test machine

| Component | Specification |
|---|---|
| Processor | 12th Gen Intel Core i3-1215U |
| Installed RAM | 8 GB DDR4 (3200 MT/s) |
| Graphics | Intel UHD Graphics |
| Storage | 477 GB |

### Reproduce the benchmark

Start the Docker deployment:

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

Run the Locust benchmark:

```bash
locust -f tests/locustfile.py --headless -u 50 -r 10 --run-time 1m --host http://localhost:8080 --csv results
```

The command generates CSV output files with request statistics.

**Benchmark limitations:** These results represent one test run on the machine listed above. They should not be interpreted as a guaranteed production capacity or as a benchmark against dedicated server hardware. Results can vary with machine load, Docker resource allocation, network conditions, and Redis performance.

## Architecture

```text
                  Client Requests
                         |
                         v
                 Nginx Reverse Proxy
                    Port 8080
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
      FastAPI 1      FastAPI 2      FastAPI 3
          |              |              |
          +--------------+--------------+
                         |
                         v
                    Redis 7
                         |
                 Atomic Lua Scripts
                         |
                         v
                Shared Rate-Limit State
```

All API instances use the same Redis-backed state. This allows a client's request quota to be enforced consistently regardless of which API instance receives the request.

Redis Lua scripts execute the rate-limit operations atomically, preventing concurrent requests from incorrectly modifying shared counters or token balances.

## Rate-Limiting Algorithms

The service implements four algorithms:

### 1. Fixed Window Counter

- Divides time into fixed intervals.
- Tracks the number of requests within each interval.
- Simple and memory-efficient.
- Can allow bursts around window boundaries.

### 2. Sliding Window Log

- Stores individual request timestamps.
- Removes timestamps outside the active time window.
- Provides accurate sliding-window enforcement.
- Requires more Redis storage than counter-based approaches.

### 3. Sliding Window Counter

- Combines the current and previous fixed-window counters.
- Estimates usage across a continuously moving time window.
- Offers a balance between memory efficiency and accuracy.

### 4. Token Bucket

- Maintains a bucket with a configurable token capacity.
- Requests consume tokens.
- Tokens refill at a configured rate.
- Supports controlled bursts while limiting sustained traffic.

## API Demonstration Endpoints

| Endpoint | Algorithm / Configuration |
|---|---|
| `/health` | Health check; excluded from rate limiting |
| `/api/v1/resource` | Sliding Window Counter; 10 requests per 10 seconds |
| `/api/v1/strict-tier` | Sliding Window Log; 3 requests per 5 seconds |
| `/api/v1/burst-tier` | Token Bucket; capacity 20, refill 2 tokens/second |
| `/api/v1/demo-cluster` | Distributed middleware demonstration |

The endpoints are intended to demonstrate different rate-limiting policies and their behavior.

## Technical Features

- FastAPI middleware for request-level enforcement.
- Redis-backed shared state across API instances.
- Atomic Lua scripts for concurrency-safe operations.
- Redis server time inside Lua scripts to avoid relying on potentially inconsistent application clocks.
- Configurable rate-limit policies.
- Custom client-key extraction.
- Tenant-based key grouping.
- Trusted proxy and forwarded-IP handling.
- API-key and user-ID based client identification.
- Configurable fail-open and fail-closed behavior.
- Rate-limit response headers.
- Lua script SHA caching and recovery from `NOSCRIPT` errors.
- Health-check endpoint excluded from rate limiting.

## Testing

The project includes automated tests for application integration, middleware behavior, dependencies, and rate-limiter management.

Previously recorded local test results:

- **23 automated tests passed**
- **98% code coverage**

Run the test suite:

```bash
python -m pytest -v
```

Run coverage:

```bash
python -m pytest --cov=rate_limiter --cov-report=term-missing -v
```

The test suite covers areas including:

- Application endpoints and integration behavior.
- Rate-limit enforcement.
- Middleware configuration and excluded paths.
- Client identification and trusted proxy handling.
- Dependency behavior and response headers.
- Redis Lua script loading and SHA caching.
- Recovery from missing Lua scripts.
- Error propagation.

*Note: The 23-test and 98%-coverage figures refer to an earlier recorded local test run. Re-run the commands above before treating them as the latest verified figures.*

## Technology Stack

| Technology | Purpose |
|---|---|
| Python 3.11+ | Core implementation |
| FastAPI | API framework |
| Redis 7 | Shared distributed state |
| Redis Lua | Atomic rate-limit operations |
| Nginx | Reverse proxy and request routing |
| Docker Compose | Multi-container deployment |
| pytest | Automated testing |
| HTTPX | API testing |
| Locust | Load testing |

## Running Locally

### Prerequisites

- Python 3.11 or later
- Redis
- Docker Desktop (recommended for the complete deployment)
- Git

### Clone the repository

```bash
git clone https://github.com/Lakshit-Patel/distributed-rate-limiter.git
cd distributed-rate-limiter
```

### Install dependencies

```bash
python -m venv .venv
```

Activate the environment on Windows Git Bash:

```bash
source .venv/Scripts/activate
```

Install the project:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### Start the distributed deployment

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

The API is exposed through Nginx at:

```text
http://localhost:8080
```

Check the health endpoint:

```bash
curl http://localhost:8080/health
```

### Stop the deployment

```bash
docker compose -f docker/docker-compose.yml down
```

## Project Structure

```text
distributed-rate-limiter/
│
├── rate_limiter/
│   ├── algorithms/
│   ├── scripts/
│   ├── middleware/
│   ├── dependencies/
│   ├── manager.py
│   └── app.py
│
├── tests/
│   ├── locustfile.py
│   ├── test_app.py
│   ├── test_dependencies.py
│   ├── test_manager.py
│   └── test_middleware.py
│
├── docker/
│   ├── docker-compose.yml
│   ├── Dockerfile
│   └── nginx configuration
│
├── docs/
│   └── INTERVIEW_NOTES.md
│
├── pyproject.toml
├── .gitignore
└── README.md
```

The directory listing above describes the main project components; individual implementation files may vary as the project evolves.

## Engineering Considerations and Limitations

This project is a portfolio-level implementation of distributed rate limiting, with a multi-instance Docker deployment and a recorded load test.

The benchmark is not a substitute for production-scale validation. Before using this service for production traffic, further work would include:

- Testing Redis failure and recovery under load.
- Measuring performance at higher concurrency levels.
- Running benchmarks on dedicated deployment hardware.
- Adding production monitoring, dashboards, and alerting.
- Evaluating Redis persistence, replication, and high availability.
- Performing security and configuration reviews.
- Testing behavior under sustained traffic and network failures.

## Learning Outcomes

Building this project involved working with:

- Distributed systems and shared state.
- Atomic operations and concurrency control.
- Redis scripting and data structures.
- API middleware and request lifecycle management.
- Rate-limiting algorithm trade-offs.
- Docker-based multi-instance deployments.
- Reverse proxy configuration.
- Automated integration testing.
- Load testing and latency analysis.

## Repository

[GitHub – Distributed Rate Limiter](https://github.com/Lakshit-Patel/distributed-rate-limiter)

