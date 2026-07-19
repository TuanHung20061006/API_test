# API Call Benchmark Final Report

Generated at: 2026-07-19 15:48-15:58 local time  
Base URL: `http://127.0.0.1:5000`  
Target endpoint: `GET /trip`  
Auth: JWT Bearer token from a temporary benchmark user  
Server: local Flask development server  

## Goal

Measure API response time and error rate using three request styles:

- synchronous sequential calls
- batch concurrent calls with a thread pool
- async concurrent calls with `httpx.AsyncClient`

Also survey the maximum number of requests a user can perform in one minute.

## Fixed-count Benchmark

Each mode called `GET /trip` 120 times.

| Mode | Requests | RPS | Avg ms | P50 ms | P95 ms | Max ms | Errors | Error Rate | Statuses |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| sync | 120 | 72.18 | 13.78 | 15.30 | 25.74 | 28.37 | 0 | 0.00% | `200: 120` |
| batch | 120 | 366.70 | 48.33 | 50.83 | 55.60 | 59.64 | 0 | 0.00% | `200: 120` |
| async | 120 | 314.83 | 56.53 | 52.66 | 85.19 | 102.21 | 0 | 0.00% | `200: 120` |

## 10-second Throughput Survey

This short survey estimates requests per minute from a 10-second window.

| Mode | Requests | Duration | RPS | Estimated Requests/Minute | Avg ms | P95 ms | Errors | Error Rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sync | 662 | 10.011s | 66.13 | 3,968 | 15.11 | 19.40 | 0 | 0.00% |
| batch, concurrency 20 | 4,000 | 10.016s | 399.38 | 23,963 | 29.12 | 46.81 | 0 | 0.00% |
| async, concurrency 20 | 3,100 | 10.007s | 309.80 | 18,588 | 51.00 | 61.54 | 0 | 0.00% |

## Batch Concurrency Sweep

This sweep tested `batch` mode at different concurrency levels for 10 seconds each.

| Concurrency | Requests | Successes | Errors | Error Rate | Estimated Successful Requests/Minute | Avg ms | P95 ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 730 | 730 | 0 | 0.00% | 4,379 | 13.57 | 25.67 |
| 2 | 1,456 | 1,456 | 0 | 0.00% | 8,716 | 9.94 | 26.94 |
| 5 | 3,825 | 3,825 | 0 | 0.00% | 22,933 | 9.53 | 13.58 |
| 10 | 3,970 | 3,970 | 0 | 0.00% | 23,801 | 15.98 | 25.52 |
| 15 | 4,170 | 4,170 | 0 | 0.00% | 24,999 | 21.59 | 34.28 |
| 20 | 4,720 | 2,125 | 2,595 | 54.98% | 12,710 | 26.09 | 44.20 |

## Full One-minute Tests

The short 10-second test can overestimate capacity, so full 60-second tests were run to find a stable one-minute result.

| Mode | Concurrency | Requests | Successes | Errors | Error Rate | Successful Requests/Minute | Avg ms | P95 ms | Error Type |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| batch stress | 20 | 29,140 | 8,182 | 20,958 | 71.92% | 8,182 | 25.30 | 44.16 | `ConnectError` |
| batch stress | 15 | 26,820 | 10,089 | 16,731 | 62.38% | 10,089 | 21.52 | 32.58 | `ConnectError` |
| batch stress | 5 | 19,220 | 16,290 | 2,930 | 15.24% | 16,290 | 11.56 | 30.07 | `ConnectError` |
| batch stable | 2 | 8,468 | 8,468 | 0 | 0.00% | 8,468 | 10.49 | 25.48 | none |

## Maximum Requests Per Minute

Best stable full-minute result observed:

```text
8,468 successful requests/minute
0 errors
0.00% error rate
```

Configuration:

```text
mode: batch
concurrency: 2
endpoint: GET /trip
duration: 60.013 seconds
```

Higher concurrency produced more raw attempts, but also produced many `ConnectError` failures against the local Flask development server. Therefore, the practical maximum observed with zero errors is `8,468 requests/minute`.

## Interpretation

- `sync` is stable but slower because it sends one request at a time.
- `batch` is fastest under controlled concurrency.
- `async` is fast, but in this local test it was slightly slower than batch for this endpoint.
- The Flask development server is not a production load-testing target. Under sustained high concurrency, it begins refusing connections.
- For a production-grade measurement, run the API behind a WSGI server such as Gunicorn or Waitress and repeat this benchmark.
