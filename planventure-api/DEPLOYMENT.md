# Staging and Production Deployment

This project uses the same application artifact in staging and production.
Environment variables select the runtime configuration; secrets must come from
the deployment platform, not from committed files.

## Required services

- PostgreSQL-compatible database
- Redis for forecast caching
- Redis for distributed rate-limit counters
- WeatherAPI account and API key
- Gemini API access and API key when trip advice is enabled
- HTTPS reverse proxy or managed ingress

The cache and rate limiter may share one Redis server, but should use separate
databases or namespaces.

## Configure an environment

Use `.env.staging.example` or `.env.production.example` as a checklist. Replace
every example hostname, password, and secret through the platform's environment
or secret settings.

Generate independent application secrets:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run the command twice: once for `SECRET_KEY` and once for `JWT_SECRET_KEY`.
Never paste the generated values into a tracked file.

The application intentionally refuses to start in staging or production when:

- either secret is missing, weak, or still uses a development value;
- `DATABASE_URL` is absent or points to SQLite;
- `CORS_ORIGINS` is absent or contains localhost;
- `WEATHER_API_KEY` is absent;
- cache or rate-limit storage is not Redis-backed.

## Gemini trip advice

Configure these deployment variables when enabling Gemini trip advice:

```text
GEMINI_ENABLED
GEMINI_API_KEY
GEMINI_MODEL
GEMINI_TIMEOUT_SECONDS
GEMINI_ADVICE_CACHE_TTL_SECONDS
GEMINI_ADVICE_RATE_LIMIT
GEMINI_ADVICE_MAX_REQUEST_BYTES
```

Supply `GEMINI_API_KEY` through the deployment environment or secret manager.
Never place it in source code, a container image, deployment manifest committed
to the repository, or frontend configuration. A deployment may keep
`GEMINI_ENABLED=false` without a Gemini key. If the feature is enabled without
a usable key, the AI advice endpoint returns `503 AI_NOT_CONFIGURED`; application
startup and non-AI endpoints remain available.

Gemini advice is a synchronous external-provider request, so latency depends on
the provider. The default timeout is 30 seconds. Planventure does not add a
custom retry loop; provider quota or rate limiting is exposed through the safe
`AI_PROVIDER_RATE_LIMITED` error code. Monitor provider usage, quota, latency,
and cost through the deployment and provider tooling.

Local development may use `SimpleCache`. Staging and production deployments
with multiple application instances must use the configured Redis cache backend
so AI advice entries are shared. AI cache entries are temporary optimizations,
not durable data. A cache read or write failure does not prevent successful
advice from being returned, and cache hits still count toward the per-user
endpoint rate limit.

This feature does not change the database schema, add a migration, store AI
history, or persist prompts and Gemini responses as long-term records.

To disable the feature quickly:

```env
GEMINI_ENABLED=false
```

Restart the deployment after changing the setting. No database rollback is
required.

## Release sequence

Install dependencies and verify the artifact:

```powershell
pip install -r requirements.txt
python -m pytest
python -m pip check
```

Apply migrations as a one-off release command:

```powershell
python -m alembic -c migrations/alembic.ini upgrade head
python -m alembic -c migrations/alembic.ini current
```

Start the web process:

```powershell
waitress-serve --host=0.0.0.0 --port=5000 wsgi:app
```

The reverse proxy or platform ingress should terminate TLS and forward requests
to port 5000.

## Post-deployment checks

1. `GET /health` returns `200` with database status `available`.
2. Registration and login return valid access and refresh tokens.
3. A protected trip request without JWT returns `401`.
4. A real trip weather request returns normalized WeatherAPI data.
5. A repeated weather request reports `meta.cached: true`.
6. Rate-limit counters are shared between application instances.
7. Responses and application logs do not contain `WEATHER_API_KEY`.
8. When Gemini advice is enabled, an owned trip returns structured JSON from
   `POST /trip/<trip_id>/ai-advice`.
9. A repeated identical AI request reports `meta.cache_hit: true`.
10. Responses and application logs do not contain `GEMINI_API_KEY`, prompts,
    authorization headers, or raw provider output.

Deploy staging first. Promote the same tested commit to production only after
the staging smoke checks pass.
