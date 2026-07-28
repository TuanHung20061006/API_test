# Planventure API

Planventure API is a Flask REST API for user authentication and trip planning. It uses SQLAlchemy for persistence, JWT for protected routes, bcrypt for password hashing, and CORS configuration for a React frontend.

## Features

- User registration with email validation
- Password hashing with bcrypt
- JWT access and refresh token generation
- Refresh endpoint for rotating access tokens
- Request rate limiting for authentication endpoints
- Database migrations and automated API tests
- Auth middleware for protected routes
- Trip CRUD endpoints
- Default itinerary template generation
- On-demand Gemini trip advice with structured responses
- Optional weather-aware AI review with per-user rate limiting and caching
- SQLite local database support
- CORS configured for React and Vite development servers

## Tech Stack

- Python
- Flask
- Flask-SQLAlchemy
- Flask-JWT-Extended
- Flask-CORS
- bcrypt
- google-genai
- Pydantic
- python-dotenv
- SQLite

## Project Structure

```text
planventure-api/
  app.py
  config.py
  extensions.py
  init_db.py
  requirements.txt
  models/
    user.py
    trip.py
  routes/
    auth.py
    trips.py
  middleware/
    auth.py
  utils/
    password.py
    jwt.py
    itinerary.py
```

## Local Setup

From the repository root:

```powershell
cd D:\python\API
.\.venv\Scripts\Activate.ps1
cd .\planventure-api
pip install -r requirements.txt
```

Create a local `.env` file from `.sample.env`:

```powershell
copy .sample.env .env
```

Example `.env`:

```env
FLASK_ENV=development
SECRET_KEY=dev-secret-key
JWT_SECRET_KEY=dev-jwt-secret-key-change-me-32-bytes-minimum
JWT_ACCESS_TOKEN_EXPIRES_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRES_DAYS=30
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173
DATABASE_URL=sqlite:///D:/python/API/planventure-api/planventure.db
```

### Weather Configuration

The trip weather endpoint uses WeatherAPI. Keep the provider key in `.env`; never
send it to the frontend or commit it to source control.

| Variable | Default | Description |
|---|---|---|
| `WEATHER_API_KEY` | empty | WeatherAPI provider key; required in production |
| `WEATHER_API_BASE_URL` | `https://api.weatherapi.com/v1` | Provider API base URL |
| `WEATHER_API_CONNECT_TIMEOUT_SECONDS` | `3.05` | Connection timeout |
| `WEATHER_API_READ_TIMEOUT_SECONDS` | `8` | Response read timeout |
| `WEATHER_CACHE_TTL_SECONDS` | `900` | Successful normalized forecast cache TTL |
| `WEATHER_RATE_LIMIT` | `30 per minute;300 per day` | Per-user weather endpoint limit |
| `CACHE_TYPE` | `SimpleCache` | Flask-Caching backend |
| `CACHE_REDIS_URL` | `redis://localhost:6379/1` | Redis URL when using `RedisCache` |
| `RATELIMIT_STORAGE_URI` | `memory://` | Flask-Limiter storage backend |

Local development can use the defaults:

```env
WEATHER_API_KEY=
WEATHER_API_BASE_URL=https://api.weatherapi.com/v1
WEATHER_API_CONNECT_TIMEOUT_SECONDS=3.05
WEATHER_API_READ_TIMEOUT_SECONDS=8
WEATHER_CACHE_TTL_SECONDS=900
WEATHER_RATE_LIMIT=30 per minute;300 per day
CACHE_TYPE=SimpleCache
CACHE_REDIS_URL=redis://localhost:6379/1
RATELIMIT_STORAGE_URI=memory://
```

The application starts without `WEATHER_API_KEY` in development and tests, but
the weather endpoint returns `503` until a key is configured. Production startup
requires the key.

## Initialize Database

Run the database initialization script:

```powershell
python init_db.py
```

For new deployments and later schema changes, use migrations:

```powershell
python -m flask --app app db upgrade
```

Expected output:

```text
Database tables created successfully.
Database: sqlite:///D:/python/API/planventure-api/planventure.db
Tables: trips, users
```

## Run the API

```powershell
python -m flask --app app run --debug
```

The API runs at:

```text
http://127.0.0.1:5000
```

## Health Check

```http
GET /health
```

Response:

```json
{
  "status": "healthy"
}
```

## Authentication

### Register

```http
POST /auth/register
```

Body:

```json
{
  "email": "user@example.com",
  "password": "test1234"
}
```

Successful response:

```json
{
  "message": "User registered successfully.",
  "user": {
    "id": 1,
    "email": "user@example.com"
  },
  "access_token": "...",
  "refresh_token": "..."
}
```

### Login

```http
POST /auth/login
```

Body:

```json
{
  "email": "user@example.com",
  "password": "test1234"
}
```

Successful response includes `access_token` and `refresh_token`.

### Current User

```http
GET /auth/me
Authorization: Bearer <access_token>
```

### Refresh Access Token

```http
POST /auth/refresh
Authorization: Bearer <refresh_token>
```

The response contains a new `access_token`.

## Trip Routes

All trip routes require:

```http
Authorization: Bearer <access_token>
```

### Create Trip

```http
POST /trip
```

Body:

```json
{
  "destination": "Da Nang, Vietnam",
  "start_date": "2026-08-01",
  "end_date": "2026-08-05",
  "coordinates": {
    "lat": 16.0471,
    "lng": 108.2068
  }
}
```

If `itinerary` is not provided, the API generates a default itinerary:

```json
[
  {
    "day": 1,
    "date": "2026-08-01",
    "title": "Day 1 in Da Nang, Vietnam",
    "activities": [],
    "notes": ""
  }
]
```

### List Trips

```http
GET /trip
Authorization: Bearer <access_token>
```

### Get Trip By ID

```http
GET /trip/1
Authorization: Bearer <access_token>
```

### Update Trip

```http
PATCH /trip/1
Authorization: Bearer <access_token>
```

Body:

```json
{
  "destination": "Hoi An, Vietnam"
}
```

`PATCH` updates selected fields. `PUT` requires destination and both dates. If
destination or dates change without an explicit itinerary, the default itinerary
is regenerated.

### Delete Trip

```http
DELETE /trip/1
Authorization: Bearer <access_token>
```

### Get Trip Weather

```http
GET /trip/1/weather
Authorization: Bearer <access_token>
```

Equivalent curl request:

```bash
curl -H "Authorization: Bearer <access-token>" http://localhost:5000/trip/1/weather
```

The authenticated user must own the trip. A missing trip and a trip owned by
another user both return `404`.

Successful response:

```json
{
  "trip": {
    "id": 1,
    "destination": "Da Nang, Vietnam",
    "start_date": "2026-08-01",
    "end_date": "2026-08-05"
  },
  "coverage": {
    "requested_from": "2026-08-01",
    "requested_through": "2026-08-05",
    "available_through": "2026-08-05",
    "complete": true
  },
  "weather": {
    "provider": "weatherapi",
    "location": {
      "name": "Da Nang",
      "region": "Da Nang",
      "country": "Vietnam",
      "latitude": 16.07,
      "longitude": 108.22,
      "timezone": "Asia/Ho_Chi_Minh",
      "localtime": "2026-08-01 09:00"
    },
    "current": {
      "updated_at": "2026-08-01 08:45",
      "temperature_c": 30.0,
      "feels_like_c": 34.0,
      "condition": "Partly cloudy",
      "condition_code": 1003,
      "humidity_percent": 70,
      "wind_kph": 12.0,
      "precipitation_mm": 0.0,
      "visibility_km": 10.0,
      "uv_index": 7.0
    },
    "forecast": [
      {
        "date": "2026-08-01",
        "temperature": {
          "min_c": 26.0,
          "max_c": 33.0,
          "average_c": 29.0
        },
        "condition": {
          "text": "Partly cloudy",
          "code": 1003
        },
        "chance_of_rain_percent": 30,
        "total_precipitation_mm": 0.5,
        "max_wind_kph": 20.0,
        "average_humidity_percent": 74,
        "uv_index": 7.0,
        "sunrise": "05:25 AM",
        "sunset": "06:18 PM"
      }
    ],
    "alerts": []
  },
  "meta": {
    "cached": false
  }
}
```

- `coverage.complete` is `true` only when the entire trip is inside WeatherAPI's
  current 14-day forecast window.
- `coverage.available_through` is the final trip date currently covered by the
  provider. It may be earlier than the trip end date.
- `meta.cached` indicates whether the normalized provider forecast came from
  cache. Every HTTP request is still counted by the per-user rate limit.

Main status codes:

- `200`: Forecast returned successfully.
- `401`: JWT is missing or invalid.
- `404`: Trip does not exist or belongs to another user.
- `422`: Trip dates or weather location cannot be forecast.
- `429`: Per-user weather request limit was exceeded.
- `503`: Weather provider is unavailable or not configured.
- `504`: Weather provider timed out.

Weather errors use a stable machine-readable code:

```json
{
  "error": {
    "code": "WEATHER_SERVICE_UNAVAILABLE",
    "message": "Dịch vụ thời tiết tạm thời không khả dụng."
  }
}
```

## Gemini Trip Advice

Authenticated users can explicitly ask Gemini to review an existing trip. The
application does not call Gemini when a trip is created, does not automatically
change the trip or itinerary, and does not store an AI conversation history.
Weather context is optional. Successful advice is strictly validated and
returned as structured JSON, cached for a configurable period, and protected by
a per-user rate limit.

### Request Trip Advice

```http
POST /trip/<int:trip_id>/ai-advice
Authorization: Bearer <access_token>
Content-Type: application/json
```

The authenticated user must own the trip. A missing trip and a trip owned by
another user both return `404`.

Example request:

```json
{
  "mode": "review",
  "language": "vi",
  "include_weather": true,
  "regenerate": false,
  "preferences": {
    "budget": "medium",
    "pace": "relaxed",
    "interests": [
      "ẩm thực",
      "văn hóa",
      "chụp ảnh"
    ],
    "transport": "xe máy",
    "dietary_requirements": [],
    "notes": "Không muốn di chuyển quá nhiều trong một ngày."
  }
}
```

Only `mode: "review"` is currently supported. Set `include_weather` to `false`
to review the trip without resolving WeatherAPI data. Set `regenerate` to
`true` to bypass an existing AI cache entry and request new advice; the request
still counts toward the per-user rate limit.

Example response:

```json
{
  "trip_id": 123,
  "mode": "review",
  "advice": {
    "summary": "Kế hoạch nhìn chung hợp lý.",
    "overall_score": 8,
    "strengths": [],
    "issues": [],
    "recommendations": [],
    "weather_advice": [],
    "packing_list": [],
    "disclaimer": "Đề xuất do AI tạo và cần được kiểm tra."
  },
  "warnings": [],
  "meta": {
    "cache_hit": false,
    "weather_cache_hit": true
  }
}
```

Expected AI error codes:

| Code | Meaning |
|---|---|
| `AI_DISABLED` | Gemini trip advice is disabled |
| `AI_NOT_CONFIGURED` | The backend does not have usable Gemini configuration |
| `AI_INVALID_REQUEST` | The request failed validation |
| `AI_UNSUPPORTED_MODE` | The requested mode is not supported |
| `AI_REQUEST_TOO_LARGE` | The request body exceeds the AI-specific limit |
| `AI_PROVIDER_AUTHENTICATION_FAILED` | Gemini rejected provider credentials or permissions |
| `AI_PROVIDER_TIMEOUT` | Gemini exceeded the configured timeout |
| `AI_PROVIDER_RATE_LIMITED` | Gemini provider quota or rate limit was reached |
| `AI_PROVIDER_UNAVAILABLE` | Gemini is temporarily unavailable |
| `AI_INVALID_RESPONSE` | Gemini output failed structured validation |
| `RATE_LIMIT_EXCEEDED` | The authenticated user exceeded the endpoint limit |

### Local Gemini Configuration

Keep the provider key only in the local `.env` file:

```env
GEMINI_ENABLED=true
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.6-flash
GEMINI_TIMEOUT_SECONDS=30
GEMINI_ADVICE_CACHE_TTL_SECONDS=3600
GEMINI_ADVICE_RATE_LIMIT=5 per hour
GEMINI_ADVICE_MAX_REQUEST_BYTES=32768
```

Fill `GEMINI_API_KEY` in the ignored local `.env` file and never commit it.
Bruno does not need the Gemini key: it sends the user's bearer token to
Planventure, and only the backend communicates with Gemini.

Recommended Bruno smoke flow:

```text
Health
→ Login
→ Create Trip
→ Trip Weather
→ Trip AI Advice
```

## Bruno Testing Flow

1. Start Flask:

```powershell
python -m flask --app app run --debug
```

2. Register or login.

3. Copy `access_token` from the response.

4. Add this header to protected requests:

```http
Authorization: Bearer <access_token>
```

5. Create a trip with `POST /trip`.

6. Use the returned `id` to get a single trip:

```http
GET http://127.0.0.1:5000/trip/1
```

7. Get normalized weather with `GET /trip/1/weather`.

8. Request structured Gemini advice with `POST /trip/1/ai-advice`.

## CORS

CORS is configured for common React local development URLs:

```text
http://localhost:3000
http://127.0.0.1:3000
http://localhost:5173
http://127.0.0.1:5173
```

Allowed request headers:

```text
Content-Type
Authorization
```

## Tests

```powershell
python -m pytest
```

GitHub Actions runs the test suite for pushes and pull requests.

## Staging and Production

Use `.env.staging.example` or `.env.production.example` as a variable checklist.
Do not deploy these example values or commit a populated `.env` file. Store real
credentials in the deployment platform's secret manager.

Both deployment modes fail at startup unless they have:

- secrets of at least 32 characters;
- a non-SQLite database;
- explicit non-localhost CORS origins;
- a WeatherAPI key;
- Redis-backed cache and rate-limit storage.

Run database migrations before starting a new application version:

```powershell
python -m alembic -c migrations/alembic.ini upgrade head
waitress-serve --host=0.0.0.0 --port=5000 wsgi:app
```

Use separate Redis databases or namespaces for cache and rate-limit counters:

```env
CACHE_TYPE=RedisCache
CACHE_REDIS_URL=redis://redis:6379/1
RATELIMIT_STORAGE_URI=redis://redis:6379/2
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for the complete staging/production checklist.

## Notes

- The base trip route is `/trip`, not `/trips`.
- Do not add a trailing slash to `/trip`.
- `.env` and local SQLite database files are ignored by git.
- Protected routes return `401` if the JWT token is missing or invalid.
- Trip lookup is scoped to the authenticated user, so users can only access their own trips.
- `/health` verifies database connectivity and returns `503` when unavailable.
- Request bodies are limited to 1 MiB by default.
