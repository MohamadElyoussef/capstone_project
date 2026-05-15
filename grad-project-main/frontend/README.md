# Uniclass Scheduler Frontend (Phase B)

Banner-style frontend built with React + TypeScript + Vite.

## Implemented Routes

- `/login`
- `/student`
- `/admin`
- default redirect to `/login`

## Features

- JWT login with role-based redirect and protected routes.
- Student registration portal:
  - search/filter panel
  - available classes table
  - add/drop actions
  - total credit summary with warnings
  - inline error banners
- Admin scheduling dashboard:
  - generate schedule
  - view unscheduled sections
  - view suggestions
  - expandable suggestion details
- Weekly timetable grid:
  - Admin: uses `/admin/schedule`
  - Student: tries `/registration/schedule`, falls back to placeholder list

## API Configuration

Default API base URL in the app is:

- `/api/v1`

Vite dev proxy forwards `/api` to:

- `http://127.0.0.1:8000`

If needed, override API base URL with `.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

## Run

```bash
npm install
npm run dev
```

## Verify

```bash
npm run build
npm run lint
```
