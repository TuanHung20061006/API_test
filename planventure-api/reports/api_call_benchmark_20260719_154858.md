# API Call Benchmark Report

- Base URL: `http://127.0.0.1:5000`
- Target endpoint: `GET /trip`
- Fixed requests per mode: `120`
- Batch size: `20`
- Async concurrency: `20`
- Throughput survey seconds per mode: `10`
- Full one-minute max test mode: `batch`
- Generated at: `2026-07-19T15:48:57`

## Fixed-count response time and error rate

| Mode | Requests | RPS | Avg ms | P50 ms | P95 ms | Max ms | Errors | Error rate | Statuses |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| sync | 120 | 72.18 | 13.78 | 15.3 | 25.74 | 28.37 | 0 | 0.0% | `{'200': 120}` |
| batch | 120 | 366.7 | 48.33 | 50.83 | 55.6 | 59.64 | 0 | 0.0% | `{'200': 120}` |
| async | 120 | 314.83 | 56.53 | 52.66 | 85.19 | 102.21 | 0 | 0.0% | `{'200': 120}` |

## Throughput survey

| Mode | Window sec | Requests | Estimated req/min | Avg ms | P95 ms | Errors | Error rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| sync | 10.011 | 662 | 3968 | 15.11 | 19.4 | 0 | 0.0% |
| batch | 10.016 | 4000 | 23963 | 29.12 | 46.81 | 0 | 0.0% |
| async | 10.007 | 3100 | 18588 | 51.0 | 61.54 | 0 | 0.0% |

## Maximum one-minute observed result

- Mode: `batch`
- Observed requests in one minute: `29140`
- Errors: `20958`
- Error rate: `71.92%`
- Average response time: `25.3 ms`
- P95 response time: `44.16 ms`
- Max response time: `73.47 ms`
- Status counts: `{'200': 8182, 'None': 20958}`

## Notes

- `sync` sends requests sequentially.
- `batch` sends requests concurrently through a thread pool.
- `async` sends requests concurrently with `httpx.AsyncClient`.
- Status codes >= 400 are counted as errors.
- This benchmark targets the authenticated `GET /trip` route, so it includes JWT auth and a database read.