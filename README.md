# AI Dashboard Builder

A Django + HTMX starter for an **AI-assisted dashboard builder** aimed at non-technical business users.

## What is implemented now

- Minimal Django project skeleton (`core/` + modular `apps/` layout)
- Dataset upload endpoint using `request.FILES`
- Pandas-based parser for `.csv`, `.xlsx`, `.xlsm`, `.json`
- Preview rendering for first 100 rows through an HTMX partial
- Example responsive Chart.js widget on the homepage
- Tailwind CSS via **build pipeline** (Tailwind CLI), not runtime CDN

## Project structure

```text
apps/
  accounts/
  workspaces/
  datasets/
  dashboards/
core/
templates/
static/
  src/
  dist/
```

## Quickstart

### 1) Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Tailwind CSS build setup

```bash
npm install
npm run tw:build
# or npm run tw:watch during development
```

### 3) Run app

```bash
python manage.py migrate
python manage.py runserver
```

Open:

- `/` for dashboard home
- `/datasets/upload/` for HTMX upload + preview

## Next implementation milestones

1. Add Django auth + django-allauth for email/social login
2. Persist uploads into `Dataset` / `DatasetVersion`
3. Run profiling + schema detection and save column metadata
4. Auto-generate starter dashboard widgets from inferred schema
5. Add saved dashboards, sharing, and plan-limit enforcement
