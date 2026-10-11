"""Locust load test suite for distributed rate limiter benchmark evaluation."""

from locust import HttpUser, task, between, constant_throughput
import random


class RateLimiterLoadTestUser(HttpUser):
    """Simulates realistic client traffic hitting global and tiered rate limits."""

    wait_time = between(0.01, 0.05)

    def on_start(self):
        """Assign unique client identities to test both per-user and per-IP isolation."""
        self.user_id = f"user_{random.randint(1, 100)}"
        self.api_key = f"key_{random.randint(1, 50)}"

    @task(3)
    def test_global_resource(self):
        """Exercise global sliding window counter middleware limit."""
        headers = {
            "X-User-ID": self.user_id,
            "X-API-Key": self.api_key,
        }
        with self.client.get("/api/v1/resource", headers=headers, catch_response=True) as response:
            if response.status_code in (200, 429):
                # 429 is an expected, correct outcome of rate limiting under load
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(2)
    def test_strict_sliding_log(self):
        """Exercise strict sliding window log dependency."""
        headers = {"X-User-ID": self.user_id}
        with self.client.get("/api/v1/strict-tier", headers=headers, catch_response=True) as response:
            if response.status_code in (200, 429):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(2)
    def test_burst_token_bucket(self):
        """Exercise token bucket burst tier."""
        headers = {"X-User-ID": self.user_id}
        with self.client.get("/api/v1/burst-tier", headers=headers, catch_response=True) as response:
            if response.status_code in (200, 429):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(1)
    def test_cluster_demo_distribution(self):
        """Hit the cluster demo endpoint to observe load-balancing across API instances."""
        with self.client.get("/api/v1/demo-cluster", catch_response=True) as response:
            if response.status_code in (200, 429):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")
