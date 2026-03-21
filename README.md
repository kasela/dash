# AI Dashboard Builder (MVP Blueprint)

A micro-SaaS for non-technical business users to upload Excel/CSV/JSON files and get instant, filterable dashboards.

## Product Promise

**Upload file → AI understands the data → dashboard generated instantly.**

## Core Stack

- **Backend:** Django, PostgreSQL, Pandas, openpyxl, django-allauth
- **Async jobs:** Celery + Redis
- **Frontend:** Django templates, HTMX, Tailwind CSS (build pipeline), Chart.js
- **Storage:** local (dev), S3/R2 (prod)

## MVP Scope (V1)

1. Signup/login + Google social login
2. Upload CSV/XLSX/XLSM/JSON
3. Parse + preview dataset (first 100 rows)
4. Auto-detect schema (date/dimension/measure/ID)
5. Generate 6–10 suggested widgets
6. Interactive filters with HTMX partial refresh
7. Save dashboard + share link
8. Basic pricing/plan limits

## App Layout

```text
apps/
  accounts/
  workspaces/
  datasets/
  profiling/
  dashboards/
  charts/
  filters/
  ai_engine/
  billing/
  api/
core/
templates/
static/
media/
```

## Implementation Notes

- Use deterministic Python logic for ingestion, schema inference, aggregation, and chart data.
- Use AI for suggestions, summaries, and recommendations—not for uncontrolled end-to-end dashboard generation.
- Keep all core entities scoped to `owner` or `workspace` for multi-tenant SaaS safety.
- Treat uploads as untrusted input; validate extension/MIME, sanitize, and enforce plan-based upload limits.

See `docs/implementation-plan.md` for detailed architecture, data model, and phased delivery.
