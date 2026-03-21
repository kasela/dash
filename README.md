# AI Dashboard Builder

A Django + HTMX starter for an **AI-assisted dashboard builder** aimed at non-technical business users.

## What is implemented now

- Minimal Django project skeleton (`core/` + modular `apps/` layout)
- Email/password auth pages (login/signup/logout)
- Dataset upload endpoint using `request.FILES`
- Pandas-based parser for `.csv`, `.xlsx`, `.xlsm`, `.json`
- Preview rendering for first 100 rows through an HTMX partial
- Example responsive Chart.js widget on the homepage
- Tailwind CSS via CDN for instant styling in constrained environments

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
```

## Quickstart

### 1) Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Run app

```bash
python manage.py migrate
python manage.py runserver
```

Open:

- `/` for dashboard home
- `/accounts/login/` and `/accounts/signup/` for auth
- `/datasets/upload/` for HTMX upload + preview

## Notes

- The base template currently uses `https://cdn.tailwindcss.com` for fast development.
- You can switch back to a build pipeline later for production optimization.
