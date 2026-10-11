# Technical Deep-Dive & System Design Interview Preparation

This document serves as an exhaustive study guide and interview handbook for the **Distributed Rate Limiter** project. It provides a file-by-file walkthrough, design rationale, 15 deep system design interview questions with model answers, and architectural limitations.

---

## 1. File-by-File Walkthrough

### Core Engine (`rate_limiter/`)
* **`base.py`**:
  * Defines `RateLimitResult` as an immutable frozen dataclass containing `allowed: bool`, `limit: int`, `remaining: int`, `retry_after: float`, and `reset_at: float`.
  * Declares `BaseRateLimiter(ABC)` with the async abstract method `allow(key, limit, window)`. This enforces the Liskov Substitution Principle (LSP), allowing any algorithm to be swapped without changing middleware or application logic.
* **`manager.py` (`LuaScriptManager`)**:
  * Manages atomic Redis script execution. Reads `.lua` scripts from disk, registers them via `SCRIPT LOAD` on Redis, and executes them with `EVALSHA <sha1>`.
  * **Self-Healing Execution**: Catches Redis `NOSCRIPT` exceptions (which occur if Redis is restarted or evicts its script cache) and transparently re-loads and re-executes the script without dropping user requests.
* **`app.py`**:
  * Production FastAPI application showcasing the distributed limiter across multiple replicas.
  * Demonstrates both global middleware rate limiting and route-level dependencies (`Depends(rate_limit(...))`).
  * Features the `/api/v1/demo-cluster` endpoint, returning the unique `INSTANCE_ID` of the container serving the request, proving that 3 independent containers enforce an identical shared quota in Redis.

### Atomic Lua Scripts (`rate_limiter/scripts/`)
* **`fixed_window.lua`**:
  * Divides the timeline into discrete buckets of size `window`. Namespaces the Redis key by the bucket epoch (`key:window_bucket`).
  * Executes `INCR` atomically and sets a safety TTL (`window * 2`).
* **`sliding_window_log.lua`**:
  * Maintains admitted request epoch timestamps as member scores inside a Redis Sorted Set (`ZSET`).
  * Removes expired timestamps using `ZREMRANGEBYSCORE key -inf (now - window)`.
  * If `ZCARD < limit`, adds `ZADD key now <member>` and refreshes key TTL.
  * If limit reached, inspects the oldest element (`ZRANGE key 0 0 WITHSCORES`) to calculate the exact fractional `retry_after`.
* **`sliding_window_counter.lua`**:
  * Stores `curr_count`, `prev_count`, and `bucket` in a Redis Hash.
  * Computes linear weighted decay: `estimated = prev_count * ((window - elapsed) / window) + curr_count`.
  * Provides smooth sliding-window protection with $O(1)$ memory and execution time.
* **`token_bucket.lua`**:
  * Stores `tokens` balance (as float) and `last_updated` (epoch) in a Redis Hash.
  * On arrival, replenishes tokens: `tokens = min(capacity, tokens + (now - last_updated) * refill_rate)`.
  * If `tokens >= requested`, deducts tokens and permits the request; otherwise calculates fractional `retry_after = (requested - tokens) / refill_rate`.

### Naive Implementation (`rate_limiter/algorithms/naive.py`)
* **`naive.py` (`NaiveGetSetLimiter`)**:
  * Intentionally separates `GET` and `SET` over the network with an async yield/delay to demonstrate Time-of-Check to Time-of-Use (TOCTOU) race conditions under concurrency. Used in tests to prove why Lua scripts are mandatory.

### Middleware & Security Layer (`rate_limiter/middleware/`)
* **`middleware.py` (`RateLimiterMiddleware`)**:
  * Pure ASGI middleware intercepting HTTP traffic. Extracts identity, evaluates quota under a strict timeout (e.g., 50ms–100ms), and injects standard RFC headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`). Emits HTTP 429 with `Retry-After`.
  * Implements `FailurePolicy` (`FAIL_OPEN` vs `FAIL_CLOSED`).
  * **Anti-Spoofing IP Extractor (`extract_client_ip`)**: Verifies that the direct socket peer (`request.client.host`) is explicitly listed in `trusted_proxies` before trusting `X-Forwarded-For` or `CF-Connecting-IP`. If untrusted, client headers are discarded.
* **`depends.py` (`rate_limit`)**:
  * FastAPI dependency factory for fine-grained per-route limits (e.g. strict authentication endpoints vs relaxed public GET endpoints).

---

## 2. Design Rationale & Architectural Decisions

1. **Why Redis + Lua over Redis INCR/GET/SET**:
   - Separate Redis calls across network hops introduce TOCTOU race conditions. Under 100 concurrent requests, multiple workers read `count < limit` before any worker writes back the incremented count.
   - Redis executes Lua scripts as a single atomic unit. No other command or script can run concurrently on that key while the script is executing.
2. **Authoritative Server Time (`redis.call('TIME')`) vs Local System Clocks**:
   - In distributed systems, worker machine clocks drift (NTP synchronization skew). Relying on client-side timestamps allows clients with skewed clocks to corrupt sliding window logs or replenish token buckets prematurely.
   - Redis server time provides a single, monotonic, tamper-proof timeline for all application nodes.
3. **Memory Management & Zero Key Leakage**:
   - Every Lua script guarantees that an active `EXPIRE` or `PEXPIRE` is set or refreshed on every write. Even if an end-user sends one request and disappears forever, the Redis key automatically expires, preventing unbounded memory growth.
4. **Proxy Trust Hierarchy**:
   - Naive rate limiters blindly trust `X-Forwarded-For`, allowing malicious actors to spoof arbitrary IP addresses (`X-Forwarded-For: 1.1.1.1`) to bypass rate limits or DoS arbitrary IP ranges.
   - We require explicit socket-peer verification against a configured CIDR/IP allowlist (`TRUSTED_PROXIES`).

---

## 3. 15 System Design Interview Questions & Model Answers

### Q1: Why do we use atomic Redis Lua scripts instead of standard Redis GET and SET operations?
**Answer:** Standard `GET` and `SET` operations require multiple network round trips between the application and Redis. Under concurrent traffic, a Time-of-Check to Time-of-Use (TOCTOU) race condition occurs: 50 requests hitting different API servers can simultaneously read `count = 9` (where `limit = 10`), pass the threshold check, and all execute `SET count = 10`, allowing 50 requests instead of 10. Redis executes Lua scripts atomically in a single event-loop turn—no intervening operations can execute. This guarantees absolute correctness under concurrency without client-side distributed locks.

### Q2: Why must we query `redis.call('TIME')` inside the Lua script instead of passing the application server's timestamp?
**Answer:** In a distributed setup with multiple API containers or servers across zones, local system clocks experience NTP clock drift (typically 10ms to hundreds of milliseconds). If client timestamps are passed to sliding window logs or token buckets, a server with a clock running 500ms ahead would replenish tokens prematurely or log future-dated events, corrupting rate calculation for all other servers. Redis server time acts as the single authoritative source of truth.

### Q3: What is the tradeoff between Fail-Open and Fail-Closed policies when Redis goes down?
**Answer:**
* **Fail-Open**: Allows requests to pass through with a degraded flag (`X-RateLimit-Degraded: true`). *Tradeoff*: Prioritizes system availability over strict rate limits. Legitimate users are not impacted by a cache blip, but downstream databases risk being overwhelmed if traffic surges during the outage.
* **Fail-Closed**: Rejects requests with HTTP 429 or 503. *Tradeoff*: Prioritizes backend infrastructure survival. Prevents cascading failures or database crashes, but creates an outage for legitimate users.
* *Production choice*: Fail-Open for general read traffic; Fail-Closed for expensive or sensitive endpoints (e.g. LLM inference, checkout, password reset).

### Q4: Explain the Fixed Window Boundary Burst problem and how to mitigate it.
**Answer:** In a Fixed Window algorithm with limit $L = 100$ per minute, an attacker can send 100 requests at 00:00:59 (the end of window 1) and another 100 requests at 00:01:01 (the start of window 2). Both windows legitimately see only 100 requests, but in the 2-second span across the boundary, the system absorbs 200 requests ($2\times L$). Mitigations include Sliding Window Log (which tracks continuous trailing windows) or Sliding Window Counter (which weights previous window counts).

### Q5: Compare Sliding Window Log vs. Sliding Window Counter. When would you choose which?
**Answer:**
* **Sliding Window Log**: Uses Redis Sorted Sets (`ZSET`). Scores are timestamps. It is 100% accurate because it tracks exact request timestamps. However, memory complexity is $O(N)$ where $N$ is the number of requests in the window. Under high throughput (e.g., 100,000 req/sec), storing 100,000 members per user consumes massive Redis RAM.
* **Sliding Window Counter**: Uses a Redis Hash with two counters (current and previous window). It approximates sliding count using weighted interpolation. Space complexity is $O(1)$ (~few bytes per key) regardless of traffic volume.
* *Decision*: Use Sliding Window Log for low-volume, high-concurrency strict quotas (e.g. credit card payment submissions); use Sliding Window Counter for high-volume general public APIs.

### Q6: How does the Token Bucket algorithm differ from Leaky Bucket, and what are its burst tradeoffs?
**Answer:** Token bucket accumulates tokens over time up to a maximum `capacity` and deducts tokens immediately upon request. It allows controlled bursts: a client that has been idle can fire up to `capacity` requests instantaneously. Leaky bucket, in contrast, smooths traffic into a constant output rate (requests enter a queue and are processed at fixed intervals), disallowing bursts entirely. Token bucket is preferred for HTTP REST APIs because real-world user activity is naturally bursty (e.g., page load requesting 15 assets simultaneously).

### Q7: How do you prevent IP spoofing when rate limiting by IP behind reverse proxies?
**Answer:** Malicious clients can inject arbitrary headers such as `X-Forwarded-For: 8.8.8.8` or `CF-Connecting-IP`. If the application naively reads the header, an attacker can bypass IP limits or cause denial-of-service against innocent IPs. To prevent this, the application must inspect the direct TCP socket peer address (`request.client.host`). Only if the direct socket peer is an approved reverse proxy (e.g., internal Nginx or cloud load balancer IP) is the header trusted. Otherwise, the header is stripped and the socket IP is used.

### Q8: How would you scale this architecture if Redis memory or CPU becomes the bottleneck?
**Answer:**
1. **Key Partitioning / Redis Cluster**: Shard rate limit keys across multiple Redis nodes using consistent hashing. Since our rate limiter uses single keys per entity (`rl:{algo}:{key}`), keys distribute evenly across cluster hash slots without cross-slot script restrictions.
2. **Local In-Memory Pre-Filtering (Hybrid L1/L2 Rate Limiting)**: Implement an in-process token bucket (e.g., in Python memory) that buffers or allows a percentage of requests locally before synchronizing in batches to Redis.
3. **Redis Read/Write Topology**: While writes must hit the primary, if using algorithms that can tolerate minor replication lag, reads could hit replicas (though for strict rate limiting, atomic single-master or Redis Cluster is preferred).

### Q9: What happens during a Redis master failover in Redis Sentinel or Cluster?
**Answer:** Redis replication is asynchronous. If the master node crashes and a replica is promoted, any writes that occurred in the few milliseconds before failover might be lost. In the context of rate limiting, this means a client might get a slightly higher quota for a window rollover (a soft fail-open event). For rate limiting, eventual consistency and slight transient leakage during failover is almost always acceptable compared to locking or strong distributed consensus (like Raft/Paxos), which would drastically reduce throughput.

### Q10: How do you ensure rate-limiting keys do not cause unbounded memory growth (memory leaks) in Redis?
**Answer:** Every Lua script that writes to Redis invokes `EXPIRE` or `PEXPIRE` with a safety TTL derived from the window duration (e.g., $2 \times \text{window} + 1\text{s}$). If a client makes a single request and never returns, Redis's passive and active key expiration automatically sweeps the key from memory. Additionally, Redis `maxmemory-policy` should be configured to `volatile-lru` or `allkeys-lru` to evict keys under emergency memory pressure.

### Q11: What are standard HTTP response headers for rate limiting and what do they communicate?
**Answer:**
* `X-RateLimit-Limit`: Maximum requests allowed in the current quota period.
* `X-RateLimit-Remaining`: Number of requests remaining for this client in the active window.
* `X-RateLimit-Reset`: Unix epoch timestamp indicating when the current window expires and quota fully refreshes.
* `Retry-After`: Sent on HTTP 429 responses, specifying the number of seconds the client must wait before making another request.
* `X-RateLimit-Degraded`: Custom observability header signaling Redis fallback/degradation.

### Q12: Why is the `SCRIPT LOAD` and `EVALSHA` pattern preferred over sending `EVAL` with the raw script body?
**Answer:** The Lua script body is several hundred bytes. Transmitting the full script string on every HTTP request consumes unnecessary network bandwidth, increases serialization overhead, and forces Redis to re-parse and compile the script string every time. With `SCRIPT LOAD`, the script is compiled once and cached by its 40-character SHA1 hash. `EVALSHA` transmits only the hash, saving network latency and CPU cycles.

### Q13: Can rate limiting be implemented at the API Gateway / Nginx layer instead of application code? What are the tradeoffs?
**Answer:**
* **API Gateway / Nginx (`limit_req`)**: Runs at the edge before requests hit application workers. Very fast and protects application servers from socket exhaustion. *Downside*: Limited business context. Nginx cannot easily inspect user subscription tiers in a database, JWT roles, or dynamically adjust limits based on customer plans without complex Lua/OpenResty scripting.
* **Application Middleware**: Has access to decoded JWT claims, database user records, dynamic tenant limits, and business logic. *Downside*: Requests consume application server worker connections and serialization overhead before being blocked.
* *Best Practice*: Coarse rate limiting at the Gateway (DoS protection by IP), fine-grained tier-based rate limiting at the application level.

### Q14: How does network latency to Redis impact API response latency, and how do we protect the API SLA?
**Answer:** Every guarded API request requires a round-trip to Redis. In the same cloud region or VPC, Redis latency is typically 0.5ms–2ms. To protect the API SLA:
1. Connect via an asynchronous Redis connection pool with keep-alive connections.
2. Wrap Redis execution with a strict `asyncio.wait_for(timeout=0.05)` (50ms timeout).
3. If Redis takes longer than 50ms, log an alert and trigger the configured `FailurePolicy` so the API never hangs waiting on Redis.

### Q15: How would you handle rate limiting for distributed WebSocket connections or streaming endpoints?
**Answer:** For WebSockets or Server-Sent Events (SSE), standard per-request HTTP middleware only evaluates the initial handshake. For long-lived connections:
1. Rate limit the initial connection handshake using Token Bucket or Sliding Window.
2. Rate limit incoming frames/messages inside the WebSocket read loop by passing the user key to the Token Bucket limiter on each message frame.
3. If rate limit is exceeded, send a throttle control frame or close the socket with WebSocket close code 1008 (Policy Violation).

---

## 4. Known Limitations & Production Improvements

1. **Redis as a Single Point of Failure (SPOF)**:
   - In basic deployments, a single Redis master handles all writes. In production, this must be deployed with Redis Sentinel or Redis Cluster across availability zones.
2. **Precision vs. Memory Tradeoff in Sliding Window**:
   - Sliding Window Counter uses linear interpolation, which assumes a uniform distribution of requests within the previous window. While memory is $O(1)$, bursty traffic near boundaries has a slight variance compared to true sliding logs.
3. **No Local In-Process Cache (L1 Cache)**:
   - Every request still queries Redis over the network. Adding a small local in-memory cache (e.g., allowing 80% of quota locally before querying Redis) would further cut network hops for high-throughput microservices.
4. **Dynamic Tier Updates**:
   - Limits are currently configured per route or via environment variables. In a multi-tenant SaaS application, limits would be dynamically retrieved from a cached tenant configuration store.
