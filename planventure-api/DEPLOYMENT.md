# Staging and Production Deployment

This project uses the same application artifact in staging and production.
Environment variables select the runtime configuration; secrets must come from
the deployment platform, not from committed files.

## Required services

- PostgreSQL-compatible database
- Redis for forecast caching
- Redis for distributed rate-limit counters
- WeatherAPI account and API key
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

Deploy staging first. Promote the same tested commit to production only after
the staging smoke checks pass.
