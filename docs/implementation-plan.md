# Implementation Plan

## 1) Positioning

This product should be positioned as an **AI Dashboard Builder for non-technical business users**, not a generic BI clone.

## 2) Target User Flow

1. User signs up (email/password or social)
2. User creates/selects workspace
3. User uploads CSV/XLSX/XLSM/JSON
4. System profiles data and infers schema
5. System suggests KPIs/widgets
6. Dashboard is generated with filters
7. User edits, saves, and shares dashboard
8. User uploads new dataset version to refresh

## 3) Functional Modules

### Authentication (`apps/accounts`)

- Django auth for email/password
- django-allauth for social auth
- Provider rollout: Google first, Microsoft next

### Workspaces (`apps/workspaces`)

- Workspace ownership/members
- Role-based permissions (owner/admin/member)

### Datasets (`apps/datasets`)

- File upload handling via `request.FILES`
- Dataset versions
- Sheet/table extraction
- Row sampling and schema metadata

### Profiling (`apps/profiling`)

- Missing values, duplicates, type confidence
- Numeric summary, categorical frequency, date ranges
- Data quality issue surfacing

### AI Engine (`apps/ai_engine`)

- Suggest dimensions/measures/KPIs
- Recommend chart templates
- Generate insight summaries and titles

### Dashboards + Charts + Filters (`apps/dashboards`, `apps/charts`, `apps/filters`)

- Starter dashboard generation
- Widget configuration persistence
- HTMX-powered partial refresh endpoints
- Chart.js rendering payloads

### Billing (`apps/billing`)

- Plan limits and usage metering
- Future: subscriptions + invoicing

## 4) Data Model (MVP)

### accounts

- `User`
- `Profile`
- `SocialAccount`

### workspaces

- `Workspace`
- `WorkspaceMember`

### datasets

- `Dataset`
- `DatasetVersion`
- `DatasetSheet`
- `DatasetColumn`
- `DatasetRowSample`
- `UploadJob`

### profiling

- `DatasetProfile`
- `ColumnProfile`
- `DataQualityIssue`

### dashboards

- `Dashboard`
- `DashboardWidget`
- `DashboardFilter`
- `DashboardShareLink`

### charts

- `ChartConfig`
- `ChartQueryCache`

### ai_engine

- `AnalysisPrompt`
- `AnalysisResult`
- `SuggestionSet`

## 5) Ingestion Pipeline

1. Validate upload (size/type/plan limits)
2. Save original file
3. Create `UploadJob` + `DatasetVersion`
4. Parse with Pandas (`read_csv`, `read_excel`, JSON loader)
5. Extract sheet/table metadata
6. Infer schema + confidence scores
7. Persist profile + samples
8. Trigger suggestion engine
9. Generate starter dashboard

## 6) Schema Detection Rules

Infer likely:

- **Date fields:** month, period, invoice_date, year
- **Dimensions:** branch, region, category, manager, product
- **Measures:** sales, quantity, cost, profit, target
- **Ratios:** margin %, growth %, achievement %
- **IDs:** invoice_no, customer_code, product_code

Mark high-cardinality dimensions and low-quality columns for UX warnings.

## 7) Dashboard Generation (Starter)

Default widget pack:

- KPI cards (totals + growth)
- Bar chart (top categories)
- Line chart (trend by date)
- Pie/doughnut (share composition)
- Table (drilldown)
- Top/bottom performance ranking

## 8) HTMX Interaction Pattern

- Filter input change triggers `hx-get`/`hx-post`
- Endpoint returns server-rendered widget partials
- Use `hx-indicator` for loading state
- Replace only impacted card regions
- Chart.js rehydrates from embedded JSON config

## 9) Reliability + Risk Controls

Key risks and mitigations:

- **Messy spreadsheets** → header row detection + sheet selection UI
- **Mixed types** → coercion with confidence + user correction
- **Large files** → async parsing and size limits per plan
- **Recompute cost** → aggregation cache and query snapshotting
- **AI overreach** → deterministic data path + explainable suggestions

## 10) Delivery Phases

### Phase 1

- Auth/social login
- Upload/parse/preview

### Phase 2

- Schema detection
- Profiling
- Suggestions + starter dashboard

### Phase 3

- Saved dashboards
- Filters + sharing
- Plan limit enforcement

### Phase 4

- AI insights/Q&A
- Scheduled refresh
- Team collaboration improvements
