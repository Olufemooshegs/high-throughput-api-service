# k6 Load Tests

Run these from Codespaces after the stack is up:

```bash
docker compose up --build
```

The default target is `http://localhost:8000`. Override it with:

```bash
BASE_URL=http://localhost:8000 k6 run loadtest/baseline_get_ramp.js
```

## Before Interpreting Results

Each API instance currently has a Postgres pool of `min=2, max=10`. With 3 API instances behind nginx, the system can open up to 30 Postgres connections total.

That may become the bottleneck before nginx or backpressure does, especially in the mixed traffic test where writes always hit Postgres. For this project test, a temporary max of 20 per instance would allow up to 60 database connections and make it easier to observe API/nginx behavior. In production, the right value should be derived from the Postgres server's `max_connections`, reserved admin connections, number of API instances, and expected query latency rather than simply turning it up.

The `/messages` endpoints are also protected by the Redis rate limiter: 20 requests per 10 seconds per client IP. Since nginx overwrites `X-Forwarded-For` with the real connecting address, a single k6 run from Codespaces correctly appears as one client IP. That is exactly what we want for spoof protection, but it means the baseline and mixed traffic tests will hit `429` quickly unless you temporarily raise the rate-limit constants for throughput testing. Keep the normal tight limit for `rate_limit.js`, because that script is meant to prove the 429 behavior.

## What To Watch

k6 always reports request rate, checks, and latency percentiles. Focus on:

- `http_reqs`: approximate achieved request volume.
- `http_req_duration`: p50, p95, and p99 tell you whether tail latency is getting ugly.
- `checks`: failed checks usually mean unexpected status codes.
- custom counters: each script records `status_200`, `status_201`, `status_404`, `status_429`, `status_503`, and `status_other`.

Status code meaning in this project:

- `200` / `201`: normal success.
- `429`: Redis rate limiter rejected the client IP.
- `503`: per-instance backpressure rejected because an API instance was full.
- `404`: usually means the message ID used by a GET test does not exist yet.

## Scripts

Baseline cached GET ramp:

```bash
MESSAGE_ID=1 k6 run loadtest/baseline_get_ramp.js
```

Mixed read/write traffic:

```bash
MESSAGE_ID=1 k6 run loadtest/mixed_traffic.js
```

Purposeful rate-limit test:

```bash
k6 run loadtest/rate_limit.js
```

Purposeful backpressure test:

```bash
BACKPRESSURE_MAX_CONCURRENT_REQUESTS=2 BACKPRESSURE_TEST_DELAY_SECONDS=2 docker compose up --build
k6 run loadtest/backpressure.js
```

The backpressure script hits `/health`, not `/messages`, so the 20 requests / 10 seconds rate limiter does not hide the 503 behavior.
