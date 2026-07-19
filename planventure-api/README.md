# Planventure API

Planventure API is a Flask REST API for user authentication and trip planning. It uses SQLAlchemy for persistence, JWT for protected routes, bcrypt for password hashing, and CORS configuration for a React frontend.

## Features

- User registration with email validation
- Password hashing with bcrypt
- JWT access and refresh token generation
- Auth middleware for protected routes
- Trip CRUD endpoints
- Default itinerary template generation
- SQLite local database support
- CORS configured for React and Vite development servers

## Tech Stack

- Python
- Flask
- Flask-SQLAlchemy
- Flask-JWT-Extended
- Flask-CORS
- bcrypt
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

## Initialize Database

Run the database initialization script:

```powershell
python init_db.py
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

### Delete Trip

```http
DELETE /trip/1
Authorization: Bearer <access_token>
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

## Notes

- The base trip route is `/trip`, not `/trips`.
- Do not add a trailing slash to `/trip`.
- `.env` and local SQLite database files are ignored by git.
- Protected routes return `401` if the JWT token is missing or invalid.
- Trip lookup is scoped to the authenticated user, so users can only access their own trips.
