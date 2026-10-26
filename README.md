# Apartment Association Platform

[![CI](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/actions/workflows/ci.yml)

A self-hosted web app for an apartment association: residents submit their water meter readings,
the board reviews them in one matrix, emergencies reach the right entrance, and neighbours talk in
a small forum without exposing their names unless they choose to.

> **Course project.** This is the final project of the
> [AI Dev Tools Zoomcamp 2026](https://courses.datatalks.club/ai-dev-tools-2026/) by DataTalksClub:
> one application specified, built, tested, containerized, deployed and monitored with AI agents.
> The course criteria and where each one is met are listed in
> [Course criteria](#course-criteria-and-where-to-find-them).

**Live demo:** https://dev.164-68-121-228.sslip.io:8443 (pick a demo role on the start screen) ·
**API docs:** https://dev.164-68-121-228.sslip.io:8443/docs

---

## The problem

In Estonia an apartment association (*korteriühistu*) is run by volunteers: a chairman, a few
board members and an accountant. Every month each apartment reports its water meter readings,
and the board turns them into bills. In practice this happens in a messenger chat and a
spreadsheet:

- readings arrive late, with typos (`1254` instead of `125.4`), and nobody notices until the bill
  is wrong;
- the chairman chases the missing apartments by hand and has to guess the rest;
- the basement master meter never matches the sum of the apartments, and nobody sees by how much;
- "hot water is off in entrance 2" goes to everybody, or to nobody;
- the chat shows everyone's full name and phone number to all neighbours.

Commercial tools exist, but they are expensive for a 30-apartment building and keep the
association's data on someone else's server.

## What the system does

One installation is one association (single-tenant, one Docker Compose project and one PostgreSQL
database), cheap enough for a small VPS.

| Who | Can do |
| :--- | :--- |
| **Resident** (owner or tenant) | Sees the meters of their apartment, last period's value and their usual consumption; submits the monthly readings; chooses how neighbours see them (`Apt. 14`, `I. P. (Apt. 14)` or the full name); reads emergencies and notices; writes in the forum. |
| **Chairman / accountant** | Sees all apartments of a period in one matrix (submitted, missing, estimated, confirmed anomaly), the master meters and the water loss; estimates missing readings from the 3-month average; corrects a reading with a mandatory reason; moves the period through *open → board review → locked*; exports a CSV for billing. The chairman also publishes like the board and can delete forum topics. |
| **Board member** | Sees the matrix; publishes official notices and emergencies for the whole building or one entrance. |

Expected behaviour that the backend enforces (tests cover each rule):

- **Two-step anomaly confirmation.** A reading below the previous one is rejected. A reading far
  above the usual consumption (above the meter limit or 3 × the 3-month average) needs two
  explicit confirmations ("are you sure?" → "double-check the digits, this goes to billing")
  before it is saved, flagged as a confirmed anomaly.
- **Exact numbers.** Meter values are decimals with 3 places end to end (`NUMERIC(12,3)`, strings
  in JSON); floats are rejected.
- **Audit trail.** Every change of a reading is logged with its old and new value, source and
  reason; board corrections cannot be overwritten by residents.
- **Privacy by design.** No API response shows a neighbour's name, email or phone. Authors appear
  as a masked badge such as `Apt. 14 • OWNER`; the mode is the resident's choice. Telemetry never
  contains personal data.
- **Targeted emergencies.** An emergency for entrance 2 is shown, and can be acknowledged, only in
  entrance 2.

Out of scope for the MVP (see [docs/ROADMAP_MVP.md](docs/ROADMAP_MVP.md) and
[docs/BACKLOG.md](docs/BACKLOG.md)): real sign-in (the demo uses a role switcher), invoices and
cost allocation, notifications, voting, backups.

## Try the demo

The demo building **KÜ Sõpruse 12** (6 apartments in 2 entrances, July–September locked, October
open) is seeded automatically. Pick a role on the start screen:

1. **Owner Apt 14**: submit October's cold water reading. A normal value is about 4.8 m³ above the
   previous one; try a much larger value to see both confirmation popups.
2. **Tenant Apt 2**: entrance 1, so the hot water emergency for entrance 2 is not shown; the forum
   shows this tenant as `M. T. (Apt. 2)` because of the chosen privacy mode.
3. **Chairman**: the readings matrix with the missing apartments, the master meter balance, the
   period status, estimates and the CSV export.

The demo data is shared by all visitors.

## Run it locally

Requirements: Docker with Compose.

```bash
docker compose up --build
```

| URL | What |
| :--- | :--- |
| http://localhost:8000 | The app (the backend serves the built client) |
| http://localhost:8000/docs | API documentation (OpenAPI) |
| http://localhost:3000 | Grafana: traces, logs, metrics and the alert (`admin` / `admin`) |

This starts PostgreSQL, the backend (migrations and demo data run on start), the OpenTelemetry
collector, Grafana LGTM and the on-call webhook receiver. Ports can be changed with
`BACKEND_PORT`, `POSTGRES_PORT` and `GRAFANA_PORT`.

## Tests

Requirements: [uv](https://docs.astral.sh/uv/) and Docker (the tests start a throwaway PostgreSQL
container; set `TEST_DATABASE_URL` to use an existing database instead).

```bash
uv sync
uv run pytest        # unit + integration tests
```

- **Unit tests**: pure domain rules (`tests/*/test_policies.py`, schemas, validation, telemetry
  scrubbing).
- **Integration tests**: the HTTP API against a real PostgreSQL (`tests/*/test_api.py`),
  migrations against the models, concurrency, the seed, the on-call agent.
- **Frontend tests**: `cd frontend && bun install && npm run test` (anomaly flow, meter values,
  locales).

CI runs all of them on every pull request.

## Architecture

```text
browser ──► Caddy (HTTPS, VPS) ──► FastAPI backend ──► PostgreSQL 16
                                    │  serves the Vue client at /
                                    │  /api/v1: property · meters · communication · demo
                                    └─ OpenTelemetry ──► collector ──► Grafana (Tempo, Loki, Prometheus)
                                                                            │ alert (5xx on readings)
                                                                            ▼
                                                    on-call agent ──► GitHub Actions + Claude Code ──► fix PR
```

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic 2, a modular monolith
  (`src/modules/property`, `meters`, `communication`; modules talk only through services and DTOs,
  side effects go through an after-commit event outbox). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Frontend**: Vue 3, Vite, Tailwind, Pinia, vue-i18n (`frontend/`). Designed in Google Stitch,
  built in Google AI Studio, synced into this repository by a workflow.
- **Database**: PostgreSQL 16 with foreign keys and CHECK constraints that keep every row inside
  its building.
- **Ops**: Docker Compose, GitHub Actions, GHCR images, a VPS with Caddy, OpenTelemetry, Grafana
  LGTM.

## How it was built: the agent team

The project is built by AI agents with defined roles ([AGENTS.md](AGENTS.md)):

| Agent | Tool | Responsibility |
| :--- | :--- | :--- |
| System architect | Claude Code | Specs in `docs/` before code: API, schema, tasks with acceptance criteria |
| Backend agent | Claude Code | `src/`, tests, migrations; strict module boundaries |
| Frontend agent | Google Stitch (mockups), Google AI Studio (Vue client) | The client in a separate repository, against `docs/openapi.json` and [docs/FRONTEND_GUIDE.md](docs/FRONTEND_GUIDE.md) |
| QA & observability | Claude Code | Integration tests, `trace_id` propagation, no PII in telemetry |
| On-call responder | Claude Code in GitHub Actions | Woken by an alert, writes a regression test and a fix, opens a PR |
| Independent reviewer | Claude Code subagent (fresh context) | Reviews a task's pull request before the merge |

The process: every task in [docs/TASKS.md](docs/TASKS.md) has a GitHub issue with acceptance
criteria → a branch → Conventional Commits → a pull request with `Closes #N` and the test output →
CI → the owner approves → merge, which deploys to dev. The frontend asks the backend for missing
data with issues labelled [`backend-request`](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues?q=label%3Abackend-request).
Frontend versions reach this repository only through the manual
[Frontend sync](.github/workflows/frontend-sync.yml) pull request
([docs/FRONTEND_GIT_WORKFLOW.md](docs/FRONTEND_GIT_WORKFLOW.md)).

## Course criteria and where to find them

| Criterion | Where |
| :--- | :--- |
| Problem description | This README: [The problem](#the-problem), [What the system does](#what-the-system-does) |
| Product specification | [docs/VISION.md](docs/VISION.md), [docs/ROADMAP_MVP.md](docs/ROADMAP_MVP.md), [docs/API.md](docs/API.md), [docs/SCHEMA.md](docs/SCHEMA.md); tasks with acceptance criteria in [docs/TASKS.md](docs/TASKS.md) and the public tracker: [issues](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues?q=is%3Aissue) and [pull requests](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/pulls?q=is%3Apr) linked by `Closes #N` |
| Agent team specification | [AGENTS.md](AGENTS.md) (roles and guardrails), [How it was built](#how-it-was-built-the-agent-team) |
| Context management | [AGENTS.md](AGENTS.md) points to the modular docs listed in [Documentation](#documentation) |
| Application | Vue client + FastAPI + PostgreSQL, one command: `docker compose up --build` |
| Testing | `uv run pytest`: unit and integration tests; frontend tests in `frontend/` |
| Containerization | [Dockerfile](Dockerfile) (client and backend in one image), [docker-compose.yml](docker-compose.yml) (database, app, observability) |
| Deployment | https://dev.164-68-121-228.sslip.io:8443 (dev) and https://164-68-121-228.sslip.io:8443 (prod); [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| CI/CD | [ci.yml](.github/workflows/ci.yml): tests on every PR, automatic deploy of `main` to dev; [deploy.yml](.github/workflows/deploy.yml): manual promotion to prod and rollback |
| Observability | Traces, metrics and JSON logs sharing one `trace_id`, PII scrubbed twice, viewable in Grafana: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) |
| Alerting on user impact | Alert on 5xx of reading submissions, per `trace_id`: [ops/alert-rules.yaml](ops/alert-rules.yaml) |
| On-call agent | [scripts/oncall_agent.py](scripts/oncall_agent.py), [oncall-responder.yml](.github/workflows/oncall-responder.yml); recorded run: alert → [workflow run](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/actions/runs/40232412197) → [fix PR #26](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/pull/26) ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md), section 5) |

## Documentation

| Document | Contents |
| :--- | :--- |
| [AGENTS.md](AGENTS.md) | Entry point for agents: roles, guardrails, doc index |
| [docs/VISION.md](docs/VISION.md) | Product vision, privacy principles, role model |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Modular monolith, module rules, event bus, plugins |
| [docs/ROADMAP_MVP.md](docs/ROADMAP_MVP.md) | MVP scope and the later phases |
| [docs/TASKS.md](docs/TASKS.md) | Implementation tasks with acceptance criteria and issue links |
| [docs/API.md](docs/API.md), [docs/openapi.json](docs/openapi.json) | REST API rules and the generated contract |
| [docs/SCHEMA.md](docs/SCHEMA.md) | Database schema |
| [docs/BACKEND_CONVENTIONS.md](docs/BACKEND_CONVENTIONS.md) | Backend coding rules (sessions, outbox, validation) |
| [docs/FRONTEND_GUIDE.md](docs/FRONTEND_GUIDE.md) | What the client must get right (decimals, popups, privacy) |
| [docs/FRONTEND_GIT_WORKFLOW.md](docs/FRONTEND_GIT_WORKFLOW.md) | AI Studio, the frontend repository, sync and previews |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | VPS, CI/CD, dev and prod, rollback |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Telemetry, the alert, the on-call agent and its demo |
| [docs/BACKLOG.md](docs/BACKLOG.md) | Known problems after the MVP |
| [docs/COURSE_REQUIREMENTS.md](docs/COURSE_REQUIREMENTS.md) | The course's project requirements |
