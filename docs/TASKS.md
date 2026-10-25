# Implementation Backlog (Phase 1 MVP)

Each task is atomic, self-contained, and designed to be executed sequentially by an AI Coding Agent.

---

### Task 1: Project Skeleton, Async DB, EventBus & OpenTelemetry Core
- **Tracking Issue:** [#2](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/2)
- **Target Module:** `src/core/`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`
- **Files to Create:**
  - `src/core/config.py`, `src/core/database.py`, `src/core/event_bus.py`, `src/core/telemetry.py`, `src/main.py`
  - `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`
- **Requirements:**
  - Setup FastAPI with SQLAlchemy 2.0 AsyncEngine (`asyncpg`) and Alembic.
  - Implement `InMemoryEventBus` supporting async pub/sub handlers.
  - Configure OpenTelemetry SDK: auto-instrument FastAPI, inject `trace_id` into structured JSON logs and `X-Trace-Id` HTTP response headers. Ensure PII scrubbing middleware strips sensitive fields.
- **Acceptance Criteria & Tests (`tests/core/`):**
  - [ ] `GET /health` returns `200 OK` and includes `X-Trace-Id` header.
  - [ ] `pytest tests/core/test_event_bus.py` verifies async event publishing and handler execution.

---

### Task 2: Module `property` & Auth — Models, RBAC & Backend Privacy Masker
- **Tracking Issue:** [#3](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/3)
- **Target Module:** `src/core/security.py`, `src/modules/property/`
- **Files to Create:**
  - `src/modules/property/models.py`, `schemas.py`, `policies.py`, `service.py`, `router.py`
  - Alembic migration `001_core_and_property.py`
- **Requirements:**
  - Create tables `core_users`, `core_user_identities`, `property_buildings`, `property_apartments`, `property_memberships`.
  - Implement pure function `format_author_badge(full_name, apt_number, relation_type, building_role, display_mode)` in `policies.py`.
  - Implement endpoints `GET /api/v1/property/me` and `PATCH /api/v1/property/memberships/{id}/privacy`.
- **Acceptance Criteria & Tests (`tests/property/`):**
  - [ ] Unit tests verify `APARTMENT_ONLY` outputs `"Кв. 14"` (`OWNER`), `INITIALS` outputs `"И. П. (Кв. 14)"`, `FULL_NAME` outputs `"Иван Петров (Кв. 14)"`.
  - [ ] Network JSON payload never contains raw `full_name`, `email`, or `phone` when `APARTMENT_ONLY` is selected.

---

### Task 3: Module `meters` (Part 1) — Models & Pure Domain Validation Policies
- **Tracking Issue:** [#4](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/4)
- **Target Module:** `src/modules/meters/`
- **Files to Create:**
  - `src/modules/meters/models.py`, `schemas.py`, `policies.py`, `events.py`
  - Alembic migration `002_meters.py`
- **Requirements:**
  - Create tables `meters_devices` (with nullable `apartment_id` for Master Meters), `meters_periods`, `meters_readings`, `meters_reading_logs`.
  - In `policies.py`, implement pure functions using `Decimal`:
    1. `validate_reading_delta(prev_value, new_value, max_delta, avg_delta, confirmation_level)` returning `OK`, `ERR_BELOW_PREV`, `WARN_STEP_1`, or `WARN_STEP_2`.
    2. `calculate_rolling_average_delta(historical_deltas, default_delta)`.
- **Acceptance Criteria & Tests (`tests/meters/test_policies.py`):**
  - [ ] Submitting `< prev_value` raises `ERR_BELOW_PREV`.
  - [ ] Submitting anomalous delta with `confirmation_level=0` returns `WARN_STEP_1`; `confirmation_level=1` returns `WARN_STEP_2`; `confirmation_level=2` returns `OK` with `is_anomaly_confirmed=True`.

---

### Task 4: Module `meters` (Part 2) — Service, Matrix API, Audit Logs & CSV Plugin
- **Tracking Issue:** [#5](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/5)
- **Target Module:** `src/modules/meters/`, `src/plugins/common/csv_export.py`
- **Files to Create:**
  - `src/modules/meters/service.py`, `router.py`
  - `src/plugins/registry.py`, `src/plugins/common/csv_export.py`
- **Requirements:**
  - Implement `POST /api/v1/meters/readings` with upsert logic on `(meter_id, period_id)` and automatic append to `meters_reading_logs`.
  - Enforce period status rules: residents can only edit while `OPEN_FOR_RESIDENTS`; Chairman/Accountant can override in `REVIEW_BY_BOARD`.
  - Implement Chairman Matrix endpoint (`GET /periods/{code}/matrix`) calculating `Master Meter - Sum(Apartment Meters)`.
  - Implement `POST /periods/{code}/estimate-missing` (`source = AUTO_ESTIMATED`) and CSV export plugin.
- **Acceptance Criteria & Tests (`tests/meters/test_api.py`):**
  - [ ] Integration test covers full lifecycle: resident submits -> updates -> audit log has 2 entries -> Chairman closes period -> auto-estimates missing apartments -> exports CSV.

---

### Task 5: Module `communication` — Targeted Emergency Alerts & Privacy-Safe Forum
- **Tracking Issue:** [#6](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/6)
- **Target Module:** `src/modules/communication/`
- **Files to Create:**
  - `src/modules/communication/models.py`, `schemas.py`, `service.py`, `router.py`, `events.py`
  - Alembic migration `003_communication.py`
- **Requirements:**
  - Implement `comm_posts` and `comm_comments`.
  - Filter `GET /emergencies/active` by resident's building, `entrance`, and `apartment_id`.
  - Resolve all post/comment authors via `PropertyService.get_masked_author_badge()` (no cross-module SQLAlchemy joins).
  - Emit `EmergencyAlertPublished` event on `EventBus` when an `EMERGENCY` post is created.
- **Acceptance Criteria & Tests (`tests/communication/`):**
  - [ ] Emergency targeted at `Entrance 2` is visible to Apt 14 (Entrance 2) and hidden from Apt 2 (Entrance 1).
  - [ ] Forum posts and comments display masked badges (`"Кв. 14 • OWNER"`) without leaking PII.

---

### Task 6: Frontend UI — Resident Mobile View & Chairman Dashboard
- **Tracking Issue:** [#7](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/7)
- **Target Module:** `src/frontend/` (served via FastAPI StaticFiles / Templates)
- **Requirements:**
  - Role switcher / Demo login bar at the top for easy grading (`Switch Role: Resident Apt 14 | Resident Apt 2 | Chairman | Accountant`).
  - Sticky red **Emergency Alert Banner** when active emergency exists for the user's entrance/building.
  - **Meter Submission Card** implementing the **Two-Popup Confirmation Flow** on `409 Conflict` (`ANOMALY_CONFIRMATION_STEP_1` -> `STEP_2` -> save).
  - **Community Micro-Forum** with inline Privacy Display Mode selector (`Кв. 14` / `И. П. (Кв. 14)` / `Иван Петров (Кв. 14)`).
  - **Chairman View:** Monthly Reading Matrix, Master Meter comparison, 1-click "Estimate Missing by Average", Reading History modal (🕒), and "Publish Emergency Alert" form.
- **Acceptance Criteria & Tests:**
  - [ ] Switching role changes the acting demo user (`X-Demo-User-Id`) and the UI shows only the views that role is allowed to use.
  - [ ] The emergency banner appears only for users in the targeted building/entrance, and "Acknowledge" hides it for that user.
  - [ ] An anomalous reading shows popup #1, then popup #2, and is saved only after the second confirmation; a normal reading saves at once.
  - [ ] The forum shows only masked author badges; changing the privacy mode updates the preview badge.
  - [ ] Chairman view: matrix shows `SUBMITTED` / `MISSING` / `AUTO_ESTIMATED` / `ANOMALY_CONFIRMED`; "Estimate Missing" fills the missing rows; the history modal shows the audit trail; the emergency form publishes an alert.
  - [ ] Frontend tests cover the two-popup confirmation flow and run with a single command.
  - [ ] Layout is usable at mobile width (375px).

---

### Task 7: Seed Data, Docker Compose & CI/CD Pipeline
- **Tracking Issue:** [#8](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/8)
- **Files to Create:** `src/core/seed.py`, `.github/workflows/ci.yml`
- **Requirements:**
  - Auto-seed a realistic demo building (`"KÜ Sõpruse 12"`) on first startup: 2 entrances, 6 apartments, Basement Master Meters, 3 historical periods (`2026-07`, `2026-08`, `2026-09`), and 1 open period (`2026-10`).
  - Configure GitHub Actions workflow running `pytest` and linting on every Pull Request.
- **Acceptance Criteria & Tests:**
  - [ ] A fresh `docker compose up --build` seeds the demo building exactly once; restarting does not duplicate data.
  - [ ] Seeded users exist for every role in the demo role switcher (Chairman, Accountant, Owner Apt 14, Owner Apt 1, Tenant).
  - [ ] Historical periods are `LOCKED` with readings; `2026-10` is `OPEN_FOR_RESIDENTS`.
  - [ ] The CI workflow runs `ruff` and `pytest` (with a PostgreSQL service) on every pull request and fails the check when a test fails.

---

### Task 8: Observability Stack, User-Impact Alerting & On-Call Responder Agent
- **Tracking Issue:** [#9](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/9)
- **Files to Create:** `ops/otel-collector-config.yaml`, `ops/alert-rules.yaml`, `scripts/oncall_agent.py`, `.github/workflows/oncall-responder.yml`
- **Requirements:**
  - Configure OpenTelemetry export (Grafana Cloud or local Jaeger/Loki/Prometheus in `docker-compose.yml`).
  - Define alert rule on `POST /api/v1/meters/readings` error rate (`HTTP 5xx > 0` or latency spike).
  - Build `scripts/oncall_agent.py`: receives alert webhook payload with `trace_id`, queries correlated error log, identifies offending file/line, runs LLM fix + `pytest`, and opens a GitHub Pull Request.
- **Acceptance Criteria & Tests:**
  - [ ] `docker compose up` also starts the collector and the trace/log/metric backends; one `POST /api/v1/meters/readings` request is viewable in the UI as a trace, metrics and logs sharing the same `trace_id`.
  - [ ] Exported telemetry contains no PII or credentials (covered by a test).
  - [ ] The alert fires when `POST /api/v1/meters/readings` returns 5xx and delivers a webhook payload containing the `trace_id`.
  - [ ] The on-call agent, triggered by that webhook, finds the correlated error log, adds a regression test, gets `pytest` green and opens a pull request that references the alert.

---

### Task 9: Deployment to VPS, CD Pipeline (dev → manual prod) & Rollback
- **Tracking Issue:** [#12](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/12)
- **Target:** `deploy/`, `.github/workflows/`, `docs/DEPLOYMENT.md`
- **Files to Create:**
  - `deploy/docker-compose.deploy.yml`, `deploy/Caddyfile`, `deploy/deploy.sh`
  - `.github/workflows/deploy.yml`, `docs/DEPLOYMENT.md`
- **Requirements:**
  - Target host: a self-hosted Linux VPS with Docker and a single public IP, shared with unrelated containers.
  - HTTPS without a purchased domain: Caddy with automatic Let's Encrypt certificates on `sslip.io` hostnames. Prod is `<ip-with-dashes>.sslip.io`, dev is `dev.<ip-with-dashes>.sslip.io`.
  - Two isolated environments on the same host (`dev`, `prod`): separate Compose projects, databases, volumes and env files.
  - Only Caddy binds host ports (`80`, `443`). All other services stay on internal Docker networks, so containers already running on the VPS are not affected.
  - Images are built once in CI, tagged with the commit SHA and pushed to GHCR. The server only pulls images by tag and never builds.
  - **CD to dev:** every push to `main` that passes CI deploys that SHA to `dev` automatically over SSH.
  - **Manual prod:** a `workflow_dispatch` run promotes a chosen, already-deployed-to-dev tag to `prod`, gated by the GitHub Environment `production`.
  - **Rollback:** `deploy.sh` records the previously deployed tag. If `/health/ready` fails after `up`, it automatically restores the previous tag. A manual rollback is the same workflow run with an older tag.
  - Migrations stay backward compatible (expand/contract), so the previous image still runs after a rollback.
  - Secrets (SSH key, database passwords) live only in GitHub Environment secrets and server-side env files, never in the repository.
- **Acceptance Criteria & Tests:**
  - [ ] Merging a PR into `main` deploys that commit to dev automatically; `https://dev.<ip-with-dashes>.sslip.io/health` returns `200` with `X-Trace-Id`.
  - [ ] Prod is deployed only through the manual workflow and serves `https://<ip-with-dashes>.sslip.io`.
  - [ ] Both URLs serve valid HTTPS certificates.
  - [ ] A deploy whose health check fails restores the previous image tag automatically; a manual rollback to an earlier tag works with one workflow run.
  - [ ] Dev and prod use separate databases; a dev deploy never touches prod data.
  - [ ] Containers on the VPS that are unrelated to this project keep running, and their ports are untouched.
  - [ ] `docs/DEPLOYMENT.md` documents the one-time VPS setup, the pipeline and the rollback procedure.
