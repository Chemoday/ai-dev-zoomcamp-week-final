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
  - [x] `GET /health` returns `200 OK` and includes `X-Trace-Id` header.
  - [x] `pytest tests/core/test_event_bus.py` verifies async event publishing and handler execution.

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
  - [x] Unit tests verify `APARTMENT_ONLY` outputs `"Apt. 14"` (`OWNER`), `INITIALS` outputs `"I. P. (Apt. 14)"`, `FULL_NAME` outputs `"Ivan Petrov (Apt. 14)"`.
  - [x] Network JSON payload never contains raw `full_name`, `email`, or `phone` when `APARTMENT_ONLY` is selected.

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
  - [x] Submitting `< prev_value` raises `ERR_BELOW_PREV`.
  - [x] Submitting anomalous delta with `confirmation_level=0` returns `WARN_STEP_1`; `confirmation_level=1` returns `WARN_STEP_2`; `confirmation_level=2` returns `OK` with `is_anomaly_confirmed=True`.

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
  - [x] Integration test covers full lifecycle: resident submits -> updates -> audit log has 2 entries -> Chairman closes period -> auto-estimates missing apartments -> exports CSV.

---

### Task 5: Module `communication` — Targeted Emergency Alerts & Privacy-Safe Forum
- **Tracking Issue:** [#6](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/6)
- **Target Module:** `src/modules/communication/`
- **Files to Create:**
  - `src/modules/communication/models.py`, `schemas.py`, `policies.py`, `service.py`, `router.py`, `events.py`
  - Alembic migration `003_communication.py`
- **Requirements:**
  - Implement `comm_posts`, `comm_comments` and `comm_emergency_acks` (the "Acknowledge" button of Task 6), plus resolving an emergency.
  - Filter `GET /emergencies/active` by resident's building, `entrance`, and `apartment_id`.
  - Resolve all post/comment authors via `PropertyService.get_masked_author_badge()` (no cross-module SQLAlchemy joins).
  - Emit `EmergencyAlertPublished` event on `EventBus` when an `EMERGENCY` post is created.
  - **Publish domain events after the transaction commits** (request-scoped outbox flushed after `get_db_session` commits). Notifications must never go out for a change that is later rolled back, and slow handlers must not hold row locks. This applies to the meters events too, which are currently published before commit.
  - Titles, bodies and comments use `src/core/validation.py` safe-text types.
- **Acceptance Criteria & Tests (`tests/communication/`):**
  - [x] Emergency targeted at `Entrance 2` is visible to Apt 14 (Entrance 2) and hidden from Apt 2 (Entrance 1).
  - [x] Forum posts and comments display masked badges (`"Apt. 14 • OWNER"`) without leaking PII.

---

### Task 6: Frontend UI — Resident Mobile View & Chairman Dashboard
- **Tracking Issue:** [#7](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/7)
- **Target Module:** `frontend/` at the repository root, built against the API contract [`docs/openapi.json`](openapi.json) (see [API.md](API.md)). How the built files are served (by the backend or by Caddy) is decided in this task.
- **Integration rules:** [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) (demo users, errors, decimal strings, the popup flow, shared demo state).
- **Requirements:**
  - Role switcher / Demo login bar at the top for easy grading (`Switch Role: Resident Apt 14 | Resident Apt 2 | Chairman | Accountant`).
  - Sticky red **Emergency Alert Banner** when active emergency exists for the user's entrance/building.
  - **Meter Submission Card** implementing the **Two-Popup Confirmation Flow** on `409 Conflict` (`ANOMALY_CONFIRMATION_STEP_1` -> `STEP_2` -> save).
  - **Community Micro-Forum** with inline Privacy Display Mode selector (`Apt. 14` / `I. P. (Apt. 14)` / `Ivan Petrov (Apt. 14)`).
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

### Task 7: Seed Data & Demo Building
- **Tracking Issue:** [#8](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/8)
- **Files to Create:** `src/core/seed.py`, `src/core/demo.py` (`GET /api/v1/demo/users` for the role switcher)
- **Requirements:**
  - Seed only when `SEED_DEMO_DATA=true` (local Docker and the public demo; off by default) and only into an empty database, so a real association never gets fictitious residents. Concurrent starts are serialized by an advisory lock. The contents are described in [API.md](API.md), section 5.
  - Auto-seed a realistic demo building (`"KÜ Sõpruse 12"`) on first startup: 2 entrances, 6 apartments, Basement Master Meters, 3 historical periods (`2026-07`, `2026-08`, `2026-09`), and 1 open period (`2026-10`).
  - _The CI workflow originally planned here moved to Task 9, which runs right after Task 1 so every later task goes through CI._
- **Acceptance Criteria & Tests:**
  - [x] A fresh `docker compose up --build` seeds the demo building exactly once; restarting does not duplicate data.
  - [x] Seeded users exist for every role in the demo role switcher (Chairman, Accountant, Owner Apt 14, Owner Apt 1, Tenant).
  - [x] Historical periods are `LOCKED` with readings; `2026-10` is `OPEN_FOR_RESIDENTS`.

---

### Task 8: Observability Stack, User-Impact Alerting & On-Call Responder Agent
- **Tracking Issue:** [#9](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/9)
- **Files to Create:** `ops/otel-collector-config.yaml`, `ops/alert-rules.yaml`, `scripts/oncall_agent.py`, `.github/workflows/oncall-responder.yml`
- **Requirements:**
  - Configure OpenTelemetry export (Grafana Cloud or local Jaeger/Loki/Prometheus in `docker-compose.yml`).
  - Define alert rule on `POST /api/v1/meters/readings` error rate (`HTTP 5xx > 0` or latency spike).
  - Build `scripts/oncall_agent.py`: receives alert webhook payload with `trace_id`, queries correlated error log, identifies offending file/line, runs LLM fix + `pytest`, and opens a GitHub Pull Request.
- **Subtasks (work strictly in order; each ends with a commit on `feat/task-8-observability` and a ticked box here, so a new session resumes at the first open box):**
  - [x] **8.1 Plan.** This list; draft PR "Closes #9" opened.
  - [x] **8.2 App exports metrics and logs over OTLP.** `src/core/telemetry.py`: a `MeterProvider` (FastAPI HTTP server metrics) and a `LoggerProvider` bridged from stdlib logging, both enabled by `OTEL_EXPORTER_OTLP_ENDPOINT`; exported log records pass the same PII scrubber as spans and JSON logs. Test: exported spans, logs and metric attributes contain no names, emails, phones or tokens.
  - [x] **8.3 Local stack.** `docker-compose.yml`: `otel-collector` (contrib image, `ops/otel-collector-config.yaml`: OTLP in, redaction processor as a second PII guard, OTLP out) and `grafana/otel-lgtm` (Grafana + Tempo + Loki + Prometheus, UI on `localhost:3000`). Backend sends to the collector. Manual check: one `POST /api/v1/meters/readings` is visible as a trace, metrics and logs with the same `trace_id`. The VPS deploy stays unchanged (shared host, low memory).
  - [x] **8.4 Alert rule.** `ops/alert-rules.yaml` (Grafana alerting provisioning): a Loki rule on 5xx log lines of `POST /api/v1/meters/readings`, grouped by `trace_id`, so the firing alert carries the `trace_id` and the error log; contact point = webhook to the on-call receiver. Add a fault switch for the demo only if a real failing input is not available.
  - [x] **8.5 On-call agent.** `scripts/oncall_agent.py`: `serve` (webhook receiver; forwards the alert as a GitHub `repository_dispatch` when `GITHUB_TOKEN` is set) and `respond` (reads the payload, extracts `trace_id`, error type and the offending file/line from the traceback, asks Claude for a regression test and a patch, runs `pytest`, opens a PR referencing the alert). Unit tests for payload parsing and traceback extraction, no network.
  - [x] **8.6 Workflow.** `.github/workflows/oncall-responder.yml`: `repository_dispatch` (`oncall-alert`) and `workflow_dispatch` with a sample payload; runs `respond` with the `ANTHROPIC_API_KEY` secret (the project owner adds it). The PR must reference the alert and pass CI.
  - [ ] **8.7 End-to-end demo and docs.** `docs/OBSERVABILITY.md`: how to open the dashboards, follow one `trace_id` across trace, logs and metrics, trigger the alert, and what the agent did (link to its PR). Tick the acceptance criteria below, mark the PR ready for review.
- **Acceptance Criteria & Tests:**
  - [ ] `docker compose up` also starts the collector and the trace/log/metric backends; one `POST /api/v1/meters/readings` request is viewable in the UI as a trace, metrics and logs sharing the same `trace_id`.
  - [ ] Exported telemetry contains no PII or credentials (covered by a test).
  - [ ] The alert fires when `POST /api/v1/meters/readings` returns 5xx and delivers a webhook payload containing the `trace_id`.
  - [ ] The on-call agent, triggered by that webhook, finds the correlated error log, adds a regression test, gets `pytest` green and opens a pull request that references the alert.

---

### Task 9: CI Pipeline, Deployment to VPS, CD (dev → manual prod) & Rollback
- **Tracking Issue:** [#12](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/12)
- **Execution order:** runs right after Task 1, so every later task goes through CI and lands on dev automatically.
- **Target:** `.github/workflows/`, `deploy/`, `docs/DEPLOYMENT.md`
- **Files to Create:**
  - `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`
  - `deploy/docker-compose.deploy.yml`, `deploy/deploy.sh`, `deploy/ssh-gate.sh`, `deploy/bootstrap.sh`, `deploy/edge/` (Caddy)
  - `src/core/migrations.py`, `docs/DEPLOYMENT.md`
- **Requirements:**
  - **CI:** `ruff` and `pytest` (with a PostgreSQL service) run on every pull request and every push to `main`.
  - Target host: a self-hosted Linux VPS with Docker and a single public IP, shared with unrelated containers.
  - HTTPS without a purchased domain: Caddy with automatic Let's Encrypt certificates on `sslip.io` hostnames. Prod is `<ip-with-dashes>.sslip.io`, dev is `dev.<ip-with-dashes>.sslip.io`.
  - Caddy serves HTTPS on `HTTPS_PORT` (target `443`). While the VPS's Outline VPN still holds `443`, it temporarily runs on `8443`; switching is one env var. Certificates are issued over port `80` (HTTP-01) in both cases.
  - Two isolated environments on the same host (`dev`, `prod`): separate Compose projects, databases, volumes and env files.
  - Only Caddy binds host ports (`80` and `HTTPS_PORT`). All other services stay on internal Docker networks, so containers already running on the VPS are not affected.
  - The CI deploy key can only run the deploy gate (SSH forced command), never an arbitrary shell.
  - Images are built once in CI, tagged with the commit SHA and pushed to GHCR. The server only pulls images by tag and never builds.
  - **CD to dev:** every push to `main` that passes CI deploys that SHA to `dev` automatically over SSH.
  - **Manual prod:** a `workflow_dispatch` run promotes a chosen, already-deployed-to-dev tag to `prod`, gated by the GitHub Environment `production`.
  - **Rollback:** `deploy.sh` records the previously deployed tag. If `/health/ready` fails after `up`, it automatically restores the previous tag. A manual rollback is the same workflow run with an older tag.
  - Migrations stay backward compatible (expand/contract), so the previous image still runs after a rollback. The startup migration step (`src/core/migrations.py`) skips with a warning instead of crashing when the database schema is newer than the image being started.
  - Secrets (SSH key, database passwords) live only in GitHub Environment secrets and server-side env files, never in the repository.
- **Acceptance Criteria & Tests:**
  - [x] CI runs `ruff` and `pytest` (with a PostgreSQL service) on every pull request and fails the check when a test fails.
  - [x] Merging a PR into `main` deploys that commit to dev automatically; `https://dev.<ip-with-dashes>.sslip.io/health` returns `200` with `X-Trace-Id`.
  - [x] Prod is deployed only through the manual workflow and serves `https://<ip-with-dashes>.sslip.io`.
  - [x] Both URLs serve valid HTTPS certificates.
  - [x] A deploy whose health check fails restores the previous image tag automatically; a manual rollback to an earlier tag works with one workflow run.
  - [x] Dev and prod use separate databases; a dev deploy never touches prod data.
  - [x] Containers on the VPS that are unrelated to this project keep running, and their ports are untouched.
  - [x] `docs/DEPLOYMENT.md` documents the one-time VPS setup, the pipeline and the rollback procedure.
