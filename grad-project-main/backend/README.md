# Uniclass Scheduler Backend

FastAPI backend scaffold with linting, formatting, and tests for Phase 0.

## Setup

1. Create a virtual environment:
   - `py -3.14 -m venv .venv`
2. Activate it:
   - PowerShell: `.\\.venv\\Scripts\\Activate.ps1`
3. Install dependencies:
   - `py -m pip install --upgrade pip`
   - `py -m pip install -r requirements.txt -r requirements-dev.txt`

## Run

- `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## Migrations and Seed (Phase 0)

- Run migrations: `alembic upgrade head`
- Run seed command: `python scripts/seed_data.py`
- Note: seeding is a placeholder in Phase 0 and only prints a message.

## Quality

- Format: `ruff format .`
- Lint: `ruff check .`
- Test: `pytest`
