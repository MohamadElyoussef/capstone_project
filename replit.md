# Uniclass Scheduler

A full-stack university management system for course registration and automated scheduling.

## Architecture

- **Backend**: FastAPI (Python) with PostgreSQL via SQLAlchemy
- **Frontend**: React 19 + TypeScript + Vite

### Project Layout

```
grad-project-main/
  backend/   - FastAPI application
  frontend/  - React/Vite application
```

## Running the Application

Two workflows run simultaneously:

1. **Start application** - Vite dev server on port 5000 (frontend)
2. **Backend API** - Uvicorn FastAPI server on port 8000 (backend)

The frontend proxies `/api` requests to the backend at `http://127.0.0.1:8000`.

## Database

Uses Replit's built-in PostgreSQL database (configured via `DATABASE_URL` env var).

Default seed accounts:
- Admin: `admin` / `Admin123!`
- Student: `student` / `Student123!`

## Key Features

- Role-based access: Admin and Student portals
- Automated schedule generation using OR-Tools constraint solver
- Student course registration with prerequisite checking
- Export schedules to PDF, DOCX, and Excel formats

## Dependencies

Backend dependencies are in `grad-project-main/backend/requirements.txt`.
Frontend dependencies are in `grad-project-main/frontend/package.json`.

Notable additions for Replit compatibility:
- `psycopg2-binary` - PostgreSQL adapter
- `ortools` - Constraint solver for scheduling

## Changes Made for Replit

1. Updated `vite.config.ts` to bind to `0.0.0.0:5000` with `allowedHosts: true`
2. Fixed `init_db.py` to use standard SQLAlchemy inserts instead of SQLite-specific `INSERT OR IGNORE`
3. Added `psycopg2-binary` and `ortools` to requirements
4. Configured publishing to run a clean frontend install with `npm ci --include=dev` before building, so Vite and TypeScript build tools are always available in deployment
