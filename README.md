# High-Throughput API Service

A FastAPI service built to handle **10,000+ requests per second** using async I/O, connection pooling, caching, and horizontal scaling. This project exists to demonstrate a solid understanding of concurrency, latency optimization, and load balancing — not just to build another CRUD API.

> 📌 **Status:** In progress. Sections marked `[TODO]` will be filled in with real numbers as the project develops.

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

**Why Redis caching, and what's the invalidation strategy?**
[TODO — e.g., TTL-based vs write-through, and why you picked one]

**Why least-connections over round-robin for the load balancer?**
[TODO — what you observed that made this the better choice]

**How is backpressure handled?**
[TODO — semaphore, queue depth limit, or 429s under load — and why]

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
│   ├── main.py              # FastAPI app entrypoint
│   ├── routes/               # API route definitions
│   ├── db.py                  # Async DB connection pool setup
│   ├── cache.py               # Redis client setup
│   └── middleware/            # Rate limiting, logging, etc.
├── loadtest/
│   ├── scenario_ramp.js      # k6 script: gradual ramp-up
│   ├── scenario_spike.js     # k6 script: sudden traffic spike
│   └── results/               # Saved test outputs and graphs
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## What I Learned

`[TODO — 3-5 bullet points on real takeaways once the project is done. This section is often what a reviewer reads first, right after the results table, so make it specific rather than generic ("I learned async is fast") — name the actual tradeoff or surprise.]`

