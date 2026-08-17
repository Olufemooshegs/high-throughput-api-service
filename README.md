# High-Throughput API Service

A FastAPI service built to handle sustained concurrent load using async I/O, connection pooling, caching, rate limiting, load balancing, and per-instance backpressure. This project exists to demonstrate real understanding of concurrency, latency, and system behavior under load, not just working CRUD endpoints.

---

## Table of Contents

- [Why This Project Exists](#why-this-project-exists)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Key Design Decisions](#key-design-decisions)
- [Known Limitations](#known-limitations)
- [Getting Started](#getting-started)
- [Load Testing](#load-testing)
- [Results](#results)
- [Project Structure](#project-structure)

---

## Why This Project Exists

Most portfolio APIs prove you can wire up routes and a database. This one is built to answer harder questions:

- What actually breaks first when traffic scales up: the app, the database, or the network?
- How do you keep p99 latency low when p50 looks fine?
- What's the real difference between "async" code and code that's *actually* non-blocking?
- What happens to a system's dependencies (cache, database, proxy) when one of them fails or gets overwhelmed, and how gracefully does everything else respond?

Every design choice below is documented with the reasoning behind it, not just the result, and backed by tests that were actually run against a live version of the system rather than assumed to work.

---

## Architecture

```
                     ┌─────────────────┐
   Clients  ──────▶  │      nginx      │   (least_conn load balancing)
                     └────────┬────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐    ┌──────────┐
        │  api1    │   │  api2    │    │  api3    │   (3 FastAPI instances)
        │          │   │          │    │          │
        │ backpres.│   │ backpres.│    │ backpres.│   (per-instance concurrency limit)
        └────┬─────┘   └────┬─────┘    └────┬─────┘
             │              │               │
             └──────────────┼───────────────┘
                             ▼
                     ┌───────────────┐
                     │  Redis Cache   │  (hot reads, rate limit state, shared across instances)
                     └───────┬────────┘
                             ▼
                     ┌───────────────┐
                     │   PostgreSQL   │  (pooled async connections, shared across instances)
                     └───────────────┘
```

**Request flow:**
1. A request hits nginx, which picks one of 3 API instances using least-connections routing.
2. That instance checks its own backpressure limiter first. If it is already handling its maximum allowed concurrent requests, it rejects immediately with a 503, before doing any other work.
3. If accepted, the request is checked against a rate limiter (sliding window log, stored in Redis, shared across all 3 instances so a client cannot dodge the limit by landing on a different instance).
4. The instance checks Redis for cached data. If present, it returns immediately without touching the database.
5. On a cache miss, the request goes to Postgres through that instance's own connection pool (never opens a new raw connection per request).
6. Nothing in this path uses blocking I/O. Every DB call, cache call, and outbound Redis call is `async`.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| API framework | FastAPI + Uvicorn | Async-native, good tooling |
| DB driver | `asyncpg` | Non-blocking Postgres driver, a sync driver like `psycopg2` would block the event loop |
| Cache | Redis (`redis-py` async client) | Sub-millisecond reads for hot paths, also backs the rate limiter |
| Load balancer | nginx | Simple, well-understood, easy to swap algorithms |
| Load testing | k6 | Scriptable, gives latency distributions, not just averages |
| Containerization | Docker + docker-compose | Reproducible environment for anyone testing this locally |

---

## Key Design Decisions

**Why async instead of threads or multiple processes?**
The bottleneck this app spends most of its time on is waiting: waiting on a Postgres query, waiting on a Redis round trip. A thread-per-request model handles that by giving every in-flight request its own OS thread, but threads are expensive to create and hold onto, so only a few hundred to a couple thousand can run comfortably before the machine itself becomes the bottleneck, even though the CPU is barely doing real work most of that time. Async I/O instead lets a single thread hold thousands of waiting requests at once, since "waiting on the database" doesn't block anything, the event loop just moves on to another request until the DB responds. The tradeoff is that this only pays off if every I/O call in the path is genuinely non-blocking. A single blocking call hidden inside an `async def` function (a sync DB driver, `time.sleep`, a sync HTTP client) stalls the entire event loop for every request on that instance, not just the one that made the call. That is why `asyncpg` and `redis.asyncio` were used deliberately instead of their sync equivalents.

**Why a fixed connection pool size (`min=2, max=10`) per instance instead of scaling it to CPU or worker count?**
A fixed pool keeps behavior simple and observable, and it protects Postgres by capping how many connections any single process can open. A pool sized dynamically off worker count only pays off once there are multiple instances to account for, since the real risk it protects against (every instance opening a large pool and collectively exceeding Postgres's connection limit) doesn't exist with a single instance. See [Known Limitations](#known-limitations) for how this plays out now that the service runs 3 instances.

**Why Redis caching, and what's the invalidation strategy?**
Two options were considered:

1. Cache-aside with TTL (what is implemented). On a cache miss, the app reads from Postgres and writes the result into Redis with a 60 second expiry. Simple to reason about and resilient, since a bug in the app cannot leave stale data in Redis forever. The tradeoff is that an update to a message would not be reflected in the cache until the TTL expires.
2. Explicit invalidation on every write. Every update or delete would remove the corresponding cache key immediately, keeping the cache always fresh. The tradeoff is that correctness now depends on the app remembering to invalidate the right key on every mutation, which is easy to miss as more endpoints are added.

Cache-aside with TTL was chosen, since this service only has create and read endpoints (no update), so the staleness window that option 1 trades away barely applies. `POST /messages` also writes straight into the cache on creation, so a newly created message is a cache hit immediately rather than waiting for its first read. This makes the actual behavior closer to a hybrid: write-through on create, cache-aside with TTL on read.

**How does the app handle Redis being unavailable?**
Every Redis call in the request path is wrapped in error handling that catches `redis.exceptions.RedisError` and falls back to treating it as a cache miss, logging a warning instead of raising. Without this, stopping the Redis container caused every read to fail with a 500, even though Postgres was completely healthy and could have served the request on its own. Redis is meant to be a performance optimization, not a hard dependency, so a Redis outage should degrade the app to "slower," not "down." This was verified directly by stopping the Redis container mid-run and confirming reads still returned 200 (with `X-Cache: MISS`) instead of failing.

**Why least-connections over round-robin for the load balancer?**
Two options were considered:

1. Round robin. nginx sends requests to each instance in strict rotation. Simple and predictable, but it balances request count, not actual load. If one instance is stuck on a slow Postgres write while the other two are idle, round robin still sends it the next request just because it is "next in line."
2. Least connections. nginx sends each new request to whichever instance currently has the fewest active connections. This accounts for actual load rather than turn-taking, which matters here because this app has genuinely mixed-latency paths: fast Redis cache hits, slower Redis misses that fall through to Postgres, rate limit checks, reads versus writes.

Least connections was chosen, since the app's latency is not uniform across requests, so balancing by actual load is a better match than balancing by turn order. In testing with fast, uniform requests (repeated GETs on the same cached message), least connections rotated across instances in a pattern that looked close to round robin, since there was nothing to differentiate load between instances at that point. The real difference between the two algorithms shows up under uneven load, which the mixed traffic load test is designed to exercise.

**How is state kept consistent across multiple API instances?**
Running 3 API instances behind nginx meant checking what state is shared versus what lives per-instance. Redis and Postgres are both shared already, so the cache and the database are naturally consistent across instances. The rate limiter needed to be verified directly, since a per-instance in-memory counter would have silently broken once traffic was split three ways: each instance would only see roughly a third of the requests and the limit would rarely trigger. This was tested by sending 25 rapid requests through nginx, spread across all 3 instances, and confirming the 21st request still returned 429. That confirmed the rate limit state is genuinely global, backed by Redis, rather than accidentally scoped to whichever instance handled a given request.

**Why is `X-Forwarded-For` trusted, and what stops it from being spoofed?**
Trusting a client-supplied `X-Forwarded-For` header directly would let any client set it to an arbitrary value and get a fresh rate limit bucket on every request, bypassing the limiter entirely. This is safe here because nginx is the only way to reach the API instances (they are not exposed directly) and is configured to overwrite `X-Forwarded-For` with `$remote_addr`, the real connecting client's IP, replacing whatever value the original client sent rather than appending to it. Because nginx sits as the sole entry point and controls this header itself, the application can trust it.

**How is backpressure handled, and how is it different from rate limiting?**
Rate limiting protects against one client sending too many requests. It does not protect against the system as a whole being overwhelmed, even by many different well-behaved clients at once, or against requests queueing silently behind an exhausted Postgres pool until latency quietly explodes. That is a separate problem, and backpressure is what handles it.

Two options were considered:

1. A bounded semaphore per instance that rejects new requests immediately once a concurrency limit is reached, before any work (DB calls, Redis calls, JSON parsing) starts.
2. Letting requests enter normally and relying on the existing Postgres pool queue, with a short timeout so a request waiting too long for a connection fails with a 503 instead of hanging indefinitely.

The bounded semaphore was chosen. The key difference is where in the request lifecycle rejection happens. Option 2 still lets every request in, spends time on parsing and any Redis work, and only fails after already spending real work and latency on it, and only for requests that actually needed Postgres. The semaphore rejects at the door, before any work begins, which protects the whole request path rather than just the database-bound one, and keeps a failing request cheap instead of expensive.

The limiter is per-instance rather than shared across instances via Redis, deliberately. Rate limiting needs to be global because it is about one client's total behavior across the whole system, no matter which instance handles a given request. Backpressure is different: it protects each instance's own local capacity, which does not require coordination with the other instances. Making it global would mean adding a Redis round trip into the hottest part of the request path for a property that is naturally already local.

The response on rejection is a 503, not a 429, since the meaning is different: 429 says "this specific client is sending too much," 503 says "this instance is overloaded right now, regardless of who is asking." The response includes a `Retry-After` header and a JSON body describing the limit that was hit, in the same style as the 429 responses from rate limiting.

This was verified in two stages. First, directly: with the concurrency limit deliberately lowered to 1 per instance, firing 10 concurrent requests through nginx produced exactly 3 successes (one per instance) and 7 immediate 503s, confirming the limiter rejects at the door rather than queueing. Second, under real load with k6, see [Results](#results) for what that revealed about nginx's own connection handling.

One tradeoff worth naming: the limiter uses a lock around a simple counter, so every request pays a small lock acquire and release just to check capacity, even when the instance is nowhere near its limit. At the concurrency levels this project targets, that cost is negligible next to a database call. At much higher scale, that lock would become a real point of contention on its own and would be worth replacing with something lock-free.

---

## Known Limitations

Documented honestly rather than left silent, since knowing where a system's edges are is part of understanding it.

- **Postgres pool sizing across instances.** Each API instance opens its own pool of `min=2, max=10` connections to Postgres. With 3 instances running, that is up to 30 concurrent connections against Postgres's connection limit, instead of the 10 a single instance would use. Fixed sizing was chosen deliberately at the single-instance stage (see Key Design Decisions), and now that multiple instances exist, this is the natural point to revisit it, ideally driven by real numbers from the load tests rather than a guess.
- **No readiness check before nginx routes traffic.** nginx waits for the API containers to start, but not for them to actually be ready to serve requests. Postgres and Redis both use health checks that the API containers depend on, so nginx effectively waits on those indirectly, but there is no direct check confirming FastAPI itself has finished booting before nginx starts sending it traffic. In practice this could cause a handful of failed requests in the first second or two after a cold start.
- **nginx's connection queueing can mask application-level backpressure under very high concurrency.** See [Results](#results) for the specific finding and why it happens.

---

## Getting Started

### Prerequisites
- Docker & docker-compose

### Run locally
```bash
git clone https://github.com/Olufemooshegs/high-throughput-api-service
cd high-throughput-api-service
docker-compose up --build
```

The API is available through nginx at `http://localhost:8000`.
Interactive docs (Swagger UI) at `http://localhost:8000/docs`.

### Try it
```bash
# create a message
curl -X POST http://localhost:8000/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"hello"}'

# read it back (first read is a cache hit, since POST writes through to Redis)
curl -i http://localhost:8000/messages/1
```

---

## Load Testing

Load tests live in `/loadtest` and use [k6](https://k6.io/). Four scripts, each testing a different concern, rather than one generic load test, since a single "hammer it and see" run doesn't tell you which part of the system is actually being measured.

```bash
# Confirms the rate limiter holds under real concurrent load, not just sequential requests
k6 run loadtest/rate_limit.js

# Confirms per-instance backpressure behavior under a concurrent burst
k6 run loadtest/backpressure.js

# Raw GET throughput ramp
MESSAGE_ID=1 k6 run loadtest/baseline_get_ramp.js

# Mixed reads, writes, cache hits, and cache misses
MESSAGE_ID=1 k6 run loadtest/mixed_traffic.js
```

**Note on the rate limiter during load testing:** the rate limiter (20 requests / 10 seconds per client IP) triggers almost immediately once k6 opens more than a couple of virtual users, since they all originate from one machine and look like a single client to nginx. That is correct behavior, but it means the baseline and mixed traffic scripts measure the rate limiter instead of raw throughput unless the limit is temporarily raised in `app/rate_limit.py` before running them.

### What's measured in each run
- **Throughput (RPS)** at increasing concurrency levels
- **Latency distribution**: p50, p95, p99, not just average, since averages hide the worst experience
- **Error rate** under sustained load, and specifically which status code appears (200, 429, 503, or a connection-level failure), since each one points to a different limiting factor
- **Point of failure**: where does the system degrade, and how (slow responses vs dropped connections vs clean rejections)?

---

## Results

> Testing was run inside a GitHub Codespace (2-core default machine), not dedicated hardware. Absolute numbers will differ on different hardware; what matters is the shape of the curve and which component becomes the limiting factor first.

| Test | Load | Result |
|---|---|---|
| Rate limiting | 5 concurrent virtual users, 20s | 40 requests succeeded, 1750 rejected with 429, matching the expected 20 requests/10s limit shared globally per client |
| Backpressure | 50 concurrent virtual users | 100% success, 0 errors, well under total capacity |
| Backpressure | 150 concurrent virtual users | 100% success, 0 errors, right at theoretical capacity (50 per instance × 3) |
| Backpressure | 300 concurrent virtual users | 0 clean 503s; latency spread from roughly 180ms median to over 25s at the tail; some requests failed at the connection level |

**Rate limiting under concurrency:** 5 virtual users hammering the same endpoint for 20 seconds produced 1790 total requests, 40 succeeded and 1750 were rejected with 429. That ratio is correct for a limit of 20 requests per 10 seconds shared globally per client across all virtual users over a 20 second run. This confirms the sliding window rate limiter, built on an atomic Redis script, holds correctly under genuine concurrent pressure rather than just sequential requests, which is exactly the condition where a naive read-then-write rate limiter would leak extra requests through.

**Backpressure under a concurrent burst:** this produced the most informative result of the load testing phase, because it did not confirm what was expected on the surface, and the reason turned out to matter more than a clean pass would have.

At 50 and 150 concurrent virtual users, below and right at the theoretical 150 total capacity across 3 instances, every request succeeded cleanly. At 300 virtual users, no clean 503s appeared at all. Instead, latency spread out dramatically and some requests failed at the connection level rather than returning any HTTP response.

The explanation: nginx queues incoming connections at the proxy layer when all backend connections are busy, rather than immediately forwarding a request to an instance that would reject it with a clean 503. A request can sit waiting in that queue for seconds before finally reaching an instance and succeeding, which is why the test showed success with extreme tail latency instead of a burst of 503s. At high enough concurrency, some of those queued requests exceeded k6's own request timeout while still waiting in nginx's queue, showing up as connection failures rather than any real response from the app.

This does not contradict the earlier direct test, which proved the application-level backpressure limiter rejects immediately and correctly in isolation. Both findings are true at once: the mechanism itself works exactly as designed, but under a large simultaneous connection burst, nginx's own connection queue sits in front of it and can delay a request long enough that the client experiences it as extreme latency rather than a fast, clean rejection.

**Practical implication:** a production setup would want nginx itself tuned to fail fast (shorter proxy timeouts, a bounded backlog) so connection pressure is rejected at the proxy layer too, instead of only at the application layer. The application is honest about its own capacity; the layer in front of it currently is not.

**Bottleneck found:** the most significant bottleneck identified so far is not inside the FastAPI application itself, it is nginx's default connection queueing behavior under a large simultaneous burst, which delays requests rather than rejecting them and masks the otherwise-correct application-level backpressure mechanism. The application's own logic (rate limiting, backpressure, cache fallback) held up correctly under every condition it was directly tested against.

---

## Project Structure

```
.
├── app/
│   ├── main.py              # FastAPI app entrypoint, middleware registration
│   ├── db.py                 # Async Postgres pool + Redis client, created in app lifespan
│   ├── rate_limit.py         # Sliding window log rate limiter (Redis sorted set + Lua script)
│   ├── backpressure.py       # Per-instance bounded concurrency limiter
│   └── routes/
│       ├── health.py          # GET /health
│       └── messages.py        # POST /messages, GET /messages/{id}
├── loadtest/
│   ├── rate_limit.js         # k6 script: triggers and verifies rate limiting under concurrency
│   ├── backpressure.js       # k6 script: triggers and verifies backpressure under a burst
│   ├── baseline_get_ramp.js  # k6 script: raw GET throughput ramp
│   ├── mixed_traffic.js      # k6 script: mixed reads/writes/cache hits/misses
│   └── lib/metrics.js        # shared status-code counters used by all scripts
├── nginx.conf                 # Load balancer config (least_conn, X-Forwarded-For handling)
├── docker-compose.yml         # nginx + 3 API instances + Postgres + Redis
├── Dockerfile
├── requirements.txt
└── README.md
```
