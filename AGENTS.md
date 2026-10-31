# AGENTS.md — AI Engineering Team & Context Protocol

All AI coding, testing, and on-call agents operating in this repository MUST follow these architectural rules and documentation pointers.

## 1. Modular Documentation Index
- [Product Vision & Privacy Rules](docs/VISION.md)
- [Modular Monolith Architecture & Domain Rules](docs/ARCHITECTURE.md)
- [MVP Scope & Roadmap](docs/ROADMAP_MVP.md)
- [Database Schema Specification](docs/SCHEMA.md)
- [REST API Contracts](docs/API.md) and the machine-readable [OpenAPI contract](docs/openapi.json)
- [Frontend Integration Guide](docs/FRONTEND_GUIDE.md) and the [Frontend Git Workflow](docs/FRONTEND_GIT_WORKFLOW.md)
- [Implementation Task Backlog](docs/TASKS.md)
- [Product Backlog: known problems after the MVP](docs/BACKLOG.md)
- [Epics after the MVP (design pages)](docs/epics/README.md) and [planning session records](docs/cli_planning/)
- [Deployment, CD & Rollback](docs/DEPLOYMENT.md)
- [Observability, Alerting & On-Call Agent](docs/OBSERVABILITY.md)

## 2. Agent Team Roles
1. **System Architect Agent:** Maintains `docs/*.md`. Any schema or API change must be documented before implementation.
2. **Core & Backend Agent:** Implements FastAPI modules (`src/core`, `src/modules/*`, `src/plugins/*`). Enforces strict module boundaries and `EventBus` decoupling.
3. **Frontend Agent:** Builds a clean, mobile-first UI in `frontend/` supporting the 2-step anomaly confirmation modals, emergency alert banners, and chairman matrix view. Works against `docs/openapi.json` and follows `docs/FRONTEND_GUIDE.md`; never changes backend code.
4. **QA & Observability Agent:** Writes unit tests for `policies.py` and integration tests for API routers. Verifies OpenTelemetry `trace_id` propagation and ensures zero PII leakage.
5. **On-Call Responder Agent:** Activated by monitoring webhooks when user-impact alerts fire. Inspects correlated logs by `trace_id`, writes a regression test, patches the bug, and submits a Pull Request.

## 3. Non-Negotiable Guardrails
- **Strict Module Encapsulation:** Never import `models.py` across different modules in `src/modules/`. Communicate exclusively via `service.py` and DTOs in `schemas.py`.
- **Precision:** Use `Decimal` / `Numeric(12, 3)` for all meter values. Never use `float`.
- **Zero PII Leak:** Never return raw `User.full_name`, `email`, or `phone` in `communication` endpoints; always resolve through the privacy badge masker. Never log PII in OpenTelemetry spans or logs.
- **Single-Command Execution:** All changes must keep `docker compose up --build` and `pytest` passing without manual intervention.

## 4. Change Workflow
- One issue, one branch, one pull request per task. While the course submission is under review,
  `main` is frozen: branch from `develop` and open pull requests into `develop`.
- During the freeze, merges into `develop` are not deployed (CD deploys only pushes to `main`); to
  try a change on dev, add the label `deploy-dev` to its pull request. The frontend sync workflow
  opens pull requests into `main`, so it is paused until the freeze ends.
- **CTO review before merge:** when a task is done and `pytest` is green, a fresh reviewer agent
  without the development context reviews only the branch commits (`git fetch` first, then `git diff origin/develop...HEAD`) and
  the current code for quality, security, bugs and the guardrails above. The implementing agent
  verifies every finding, fixes the confirmed ones in the same branch, and lists all findings
  (fixed / rejected and why) in the pull request. The owner approves the merge.