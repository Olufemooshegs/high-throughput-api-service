# High-Throughput API Service

A FastAPI service built to handle **10,000+ requests per second** using async I/O, connection pooling, caching, and horizontal scaling. This project exists to demonstrate a solid understanding of concurrency, latency optimization, and load balancing — not just to build another CRUD API.

> 📌 **Status:** In progress. Steps 1-6 (skeleton, PostgreSQL, Redis caching, rate limiting, nginx load balancing across 3 instances, per-instance backpressure) are built and tested. Load testing (step 7) is the remaining piece before this is portfolio-ready.

---

## Table of Contents

- [Why This Project Exists](#why-this-project-exists)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Key Design Decisions](#key-design-decisions)
- [Getting Started](#getting-started)
- [Load Testing](#load-testing)
- [Results](#results)
- [Project Structure](#project-structure)
- [What I Learned](#what-i-learned)

---

## Why This Project Exists

Most portfolio APIs prove you can wire up routes and a database. This one is built to answer harder questions:

- What actually breaks first when traffic scales up — the app, the database, or the network?
- How do you keep p99 latency low when p50 looks fine?
- What's the real difference between "async" code and code that's *actually* non-blocking?

Every design choice below is made with those questions in mind, and documented so the reasoning is visible, not just the result.

---

## Architecture

```
                     ┌─────────────────┐
   Clients  ──────▶  │  Load Balancer  │   (nginx / Envoy)
                     └────────┬────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐    ┌──────────┐
        │ FastAPI  │   │ FastAPI  │    │ FastAPI  │   (N instances, async workers)
        │ instance │   │ instance │    │ instance │
        └────┬─────┘   └────┬─────┘    └────┬─────┘
             │              │               │
             └──────────────┼───────────────┘
                             ▼
                     ┌───────────────┐
                     │  Redis Cache   │  (hot reads, rate limiting)
                     └───────┬────────┘
                             ▼
                     ┌───────────────┐
                     │   PostgreSQL   │  (pooled async connections)
                     └───────────────┘
```

**Request flow in plain English:**
1. A request hits the load balancer, which picks an instance (least-connections routing).
2. The FastAPI instance checks Redis first for cached data — if it's there, it returns immediately without touching the database.
3. On a cache miss, the request goes to Postgres through a connection pool (never opens a new raw connection per request).
4. Nothing in this path uses blocking I/O — every DB call, cache call, and outbound HTTP call is `async`.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| API framework | FastAPI + Uvicorn/Gunicorn | Async-native, good tooling, honest about where the GIL bites you |
| DB driver | `asyncpg` | Non-blocking Postgres driver — `psycopg2` would block the event loop |
| Cache | Redis (`redis-py` async client) | Sub-millisecond reads for hot paths |
| Load balancer | nginx | Simple, well-understood, easy to swap algorithms |
| Load testing | k6 | Scriptable, gives latency distributions not just averages |
| Profiling | py-spy | Flame graphs without modifying code |
| Containerization | Docker + docker-compose | Reproducible environment for anyone testing this locally |

---

## Key Design Decisions

**Why async instead of just adding more threads/processes?**
[TODO — one paragraph: the tradeoff you found between concurrency model and memory/CPU cost]

**Why a fixed connection pool size (min=2, max=10) instead of scaling it to CPU/worker count?**
At single-instance stage, a fixed pool keeps the behavior simple and observable. It also protects Postgres by capping how many connections this process can open. A pool sized off worker count only becomes useful once there are multiple instances to account for, since the real risk (every instance opening its own large pool and collectively exceeding Postgres's connection limit) does not exist yet with one instance. This will be revisited at step 5, once nginx and multiple API instances are added, since that is the point where sizing actually needs to account for the total across instances.

**Why Redis caching, and what's the invalidation strategy?**
Two options were considered for keeping cached data correct:

1. Cache-aside with TTL (what is implemented). On a cache miss, the app reads from Postgres and writes the result into Redis with a 60 second expiry. Simple to reason about and resilient, since a bug in the app cannot leave stale data in Redis forever. The tradeoff is that an update to a message would not be reflected in the cache until the TTL expires.
2. Explicit invalidation on every write. Every update or delete would delete (or overwrite) the corresponding cache key immediately, keeping the cache always fresh. The tradeoff is correctness now depends on the app remembering to invalidate the right key on every mutation, which is an easy thing to miss as more endpoints are added.

Cache-aside with TTL was chosen for now, since this project only has create and read endpoints (no update yet), so the staleness window that option 1 trades away barely applies. `POST /messages` also writes straight into the cache on creation, so a newly created message is a cache hit immediately rather than waiting for its first read. This makes the current setup closer to a hybrid: write-through on create, cache-aside with TTL on read.

**How does the app handle Redis being unavailable?**
Every Redis call in the request path is wrapped in a try/except that catches `redis.exceptions.RedisError` and falls back to treating it as a cache miss, logging a warning instead of raising. This was added after testing showed that without it, stopping the Redis container caused every read to fail with a 500, even though Postgres was completely healthy and could have served the request on its own. The cache is meant to be a performance optimization, not a hard dependency, so a Redis outage should degrade the app to "slower" rather than "down." This was verified by stopping the Redis container mid-run and confirming reads still returned 200 (with `X-Cache: MISS`) instead of failing.

**Why least-connections over round-robin for the load balancer?**
Two options were considered:

1. Round robin. nginx sends requests to each instance in strict rotation (1, 2, 3, 1, 2, 3...). Simple to explain and predictable, but it balances request count, not actual load. If one instance is stuck on a slow Postgres write while the other two are idle, round robin will still send it the next request just because it is "next in line."
2. Least connections. nginx sends each new request to whichever instance currently has the fewest active connections. This accounts for actual load rather than just turn-taking, which matters here because this app has genuinely mixed-latency paths: fast Redis cache hits, slower Redis misses that fall through to Postgres, rate limit checks, and reads versus writes. The tradeoff is it is slightly more dynamic and less obviously "rotational" when watched manually.

Least connections was chosen, since the app's latency is not uniform across requests, so balancing by actual load is a better match than balancing by turn order.

In testing with fast, uniform requests (repeated GETs on the same cached message), least connections rotated across instances in a pattern that looked close to round robin, since there was nothing to differentiate load between instances. That is expected: with all requests finishing at similar speed, least connections has little to react to. The real difference between the two algorithms would show up under uneven load (e.g. mixing slow write-heavy requests with fast cached reads), which is a good candidate for the load testing phase.

**How is state kept consistent now that there are multiple API instances?**
Adding nginx and 3 API instances meant checking what state is shared versus what lives per-instance. Redis and Postgres were already shared from steps 2 and 3, so both the cache and the database are naturally consistent across instances. The rate limiter from step 4 was the one piece that needed verifying directly, since a per-instance in-memory counter would have silently broken once traffic was split three ways: each instance would only see roughly a third of the requests and the limit would rarely, if ever, trigger. This was tested directly by sending 25 rapid requests through nginx (which spread them across all 3 instances) and confirming the 21st request still returned 429. That confirmed the rate limit state is genuinely global, backed by Redis, rather than accidentally scoped to whichever instance handled a given request.

**Why is X-Forwarded-For trusted now, when it was deliberately ignored in step 4?**
In step 4, the rate limiter ignored the `X-Forwarded-For` header entirely, because nothing sat in front of the API to guarantee it was accurate. A client could set that header to any value it wanted and get a fresh rate limit bucket on every request, bypassing the limiter completely. Once nginx was added as the single entry point in step 5, this changed: nginx is configured to set `X-Forwarded-For` to `$remote_addr` (the real connecting client's IP), which replaces whatever value the original client sent rather than appending to it. Since nginx is now the only way to reach the API instances (they are not exposed directly), the header can be trusted again, and the rate limiter was updated to read it.

**How is backpressure handled?**
Rate limiting (step 4) protects against one client sending too many requests. It does not protect against the system as a whole being overwhelmed, even by many different well-behaved clients at once, or against requests queueing silently behind an exhausted Postgres pool until latency quietly explodes. That is a separate problem, and backpressure is what handles it.

Two options were considered:

1. A bounded semaphore per instance that rejects new requests immediately once a concurrency limit is reached, before any work (DB calls, Redis calls, JSON parsing) starts.
2. Letting requests enter normally and rely on the existing Postgres pool queue, but adding a short timeout so a request waiting too long for a connection fails with a 503 instead of hanging indefinitely.

The bounded semaphore was chosen. The key difference is where in the request lifecycle the rejection happens. Option 2 still lets every request in, spend time on parsing and any Redis work, and only fail after already spending real work and latency on it, and only for requests that actually needed Postgres. The semaphore rejects at the door, before any work begins, which protects the whole request path rather than just the database-bound one, and keeps a failing request cheap instead of expensive.

The limiter is per-instance rather than shared across instances via Redis. This is a deliberate difference from rate limiting, not an oversight. Rate limiting needed to be global because it is about one client's total behavior across the whole system, no matter which instance happens to handle a given request. Backpressure is different: it protects each instance's own local capacity, and that does not require coordination with the other instances. Making it global would mean adding a Redis round trip into the hottest part of the request path, for a property that is naturally already local.

The response on rejection is a 503, not a 429, since the meaning is different: 429 says "this specific client is sending too much," 503 says "this instance is overloaded right now, regardless of who is asking." The response includes a `Retry-After` header and a JSON body describing the limit that was hit, in the same style as the 429 responses from rate limiting.

This was tested by deliberately lowering the concurrency limit to 1 per instance (`BACKPRESSURE_MAX_CONCURRENT_REQUESTS=1`) and firing 10 concurrent requests through nginx. With 3 instances behind the load balancer, the result was 3 successful requests (one per instance, whichever request grabbed that instance's single slot first) and 7 immediate 503s, confirming the limiter rejects at the door rather than queueing.

One tradeoff worth naming: the limiter uses a lock around a simple counter, so every request pays a small lock acquire and release just to check capacity, even when the instance is nowhere near its limit. At the concurrency levels this project targets, that cost is negligible next to a database call. At a much higher scale, that lock would become a real point of contention on its own and would be worth replacing with something lock-free. Not a problem today, but worth knowing where the ceiling of this specific implementation is.

**Known gaps to revisit**
A couple of things were noticed while building step 5 that are not fixed yet, but are worth tracking honestly rather than ignoring:

- **Postgres pool sizing.** Each API instance still opens its own pool of `min=2, max=10` connections to Postgres. With 3 instances running, that is now up to 30 concurrent connections against Postgres's connection limit, instead of the 10 a single instance would use. This was the exact scenario flagged back in step 2 as the reason to defer CPU/worker-scaled pool sizing. It has not caused a problem yet, but should be revisited before load testing, since it is the kind of limit that only becomes visible once real concurrent traffic is pushed through the system.
- **No readiness check before nginx routes traffic.** nginx currently waits for the API containers to start (`depends_on`), but not for them to actually be ready to serve requests. Postgres and Redis both use `condition: service_healthy` so nginx effectively waits on those indirectly through the API containers, but there is no direct health check confirming FastAPI itself has finished booting before nginx starts sending it traffic. In practice this could cause a handful of failed requests in the first second or two after a cold start. Not fixed yet, but noted here rather than left silent.

---

## Getting Started

### Prerequisites
- Docker & docker-compose
- Python 3.11+ (if running outside Docker)

### Run locally
```bash
git clone <your-repo-url>
cd <repo-name>
docker-compose up --build
```

The API will be available at `http://localhost:8000`.
Interactive docs (Swagger UI) at `http://localhost:8000/docs`.

### Run without Docker
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000
```

---

## Load Testing

Load tests live in `/loadtest` and use [k6](https://k6.io/).

### Run a test
```bash
k6 run loadtest/scenario_ramp.js
```

### What's being measured
- **Throughput (RPS)** at increasing concurrency levels
- **Latency distribution** — p50, p95, p99 (not just average, since averages hide the worst experience)
- **Error rate** under sustained load
- **Point of failure** — where does the system degrade, and how does it degrade (slow responses vs dropped connections vs 5xx errors)?

---

## Results

> Numbers below are from testing on `[TODO: hardware spec, e.g. 4 vCPU / 8GB RAM]`. Reproducing this on different hardware will give different absolute numbers — what matters is the shape of the curve.

| Concurrency | RPS | p50 latency | p95 latency | p99 latency | Error rate |
|---|---|---|---|---|---|
| [TODO] | [TODO] | [TODO] | [TODO] | [TODO] | [TODO] |

**RPS vs Latency graph:** `[TODO: insert graph from /loadtest/results]`

**Bottleneck found:** `[TODO — describe the specific bottleneck you found while profiling, e.g. "DB connection pool exhausted at ~40 concurrent connections, causing queueing that spiked p99 to 800ms. Fixed by increasing pool size and adding a query timeout." This is the most important part of this README — a real before/after beats a bigger peak number.]`

---

## Project Structure

```
.
├── app/
│   ├── main.py              # FastAPI app entrypoint, middleware registration
│   ├── db.py                 # Async Postgres pool + Redis client, created in lifespan
│   ├── rate_limit.py         # Sliding window log rate limiter (Redis sorted set + Lua script)
│   ├── backpressure.py       # Per-instance bounded concurrency limiter
│   └── routes/
│       ├── health.py          # GET /health
│       └── messages.py        # POST /messages, GET /messages/{id} (cache + DB + rate limit wiring)
├── loadtest/
│   ├── scenario_ramp.js      # k6 script: gradual ramp-up
│   ├── scenario_spike.js     # k6 script: sudden traffic spike
│   └── results/               # Saved test outputs and graphs
├── nginx.conf                 # Load balancer config (least_conn, X-Forwarded-For handling)
├── docker-compose.yml         # nginx + 3 API instances + Postgres + Redis
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## What I Learned

`[TODO — 3-5 bullet points on real takeaways once the project is done. This section is often what a reviewer reads first, right after the results table, so make it specific rather than generic ("I learned async is fast") — name the actual tradeoff or surprise.]`