# Distributed Rate Limiter – Testing Evidence

## 1. Automated Test Execution

**Description:**

The distributed rate limiter was validated using automated Pytest test cases covering its core algorithms, concurrency handling, middleware, Redis integration, and API behavior.

The test execution completed successfully with **23 tests passed in 6.91 seconds**.

**Evidence:** Screenshot 1 – Automated test execution results.
<img width="959" height="360" alt="Screenshot 2026-11-25 113511" src="https://github.com/user-attachments/assets/e9e857f8-1605-4f5e-8c55-0d5dee1426a0" />
<img width="959" height="360" alt="Screenshot 2026-11-25 113511" src="https://github.com/user-attachments/assets/aa57c499-8ccc-4951-bf53-f8bf984cc679" />
<img width="959" height="293" alt="Screenshot 2026-11-25 113049" src="https://github.com/user-attachments/assets/f4370621-df59-44a2-bd84-c659ded3070b" />
<img width="949" height="539" alt="Screenshot 2026-11-25 113043" src="https://github.com/user-attachments/assets/9d1e891e-1641-404b-8696-991106cb0bee" />


---

## 2. Code Coverage Report

**Description:**

Code coverage was measured using Pytest-Cov to evaluate how much of the rate limiter implementation was exercised by the automated test suite.

The report covers **296 statements**, with **98% overall code coverage** and only 6 statements missed.

**Evidence:** Screenshot 2 – Pytest-Cov coverage report.

---

## 3. Concurrent Request Testing

**Description:**

Evidence of concurrent request testing using 100 parallel HTTP requests against the burst-tier API endpoint.

The test records the HTTP response status distribution to evaluate rate-limit enforcement under concurrent traffic.

**Evidence:** Screenshot 3 – Concurrent request status distribution.

---

## 4. HTTP 429 Rate-Limit Enforcement

**Description:**

Demonstrates the API's rate-limit enforcement behavior when the configured request threshold is exceeded.

The response headers provide visibility into the configured request limit, remaining quota, and retry interval.

**Evidence:** Screenshot 4 – HTTP 429 response and rate-limit headers.
