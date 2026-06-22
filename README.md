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

## Getting Started

### Prerequisites

- Python >= 3.12
- Node.js 20
- PostgreSQL (set via `DATABASE_URL` env var)

### Install

```bash
# Backend
cd grad-project-main/backend
pip install -r requirements.txt

# Frontend
cd grad-project-main/frontend
npm install
```

### Run

Two services run simultaneously:

1. **Frontend** - Vite dev server on port 5000
2. **Backend** - Uvicorn FastAPI server on port 8000

```bash
# Backend
cd grad-project-main/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd grad-project-main/frontend
npm run dev
```

The frontend proxies `/api` requests to the backend at `http://127.0.0.1:8000`.

### Build

```bash
npm run build
```

## Database

Configured via the `DATABASE_URL` environment variable (PostgreSQL).

Default seed accounts:
- Admin: `admin` / `Admin123!`

## Key Features

- Role-based access: Admin and Student portals
- Automated schedule generation using OR-Tools constraint solver
- Student course registration with prerequisite checking
- Export schedules to PDF, DOCX, and Excel formats

## Dependencies

- Backend: see `grad-project-main/backend/requirements.txt` (FastAPI, SQLAlchemy, Alembic, OR-Tools, pandas, openpyxl, passlib, python-jose, psycopg2-binary)
- Frontend: see `grad-project-main/frontend/package.json`
