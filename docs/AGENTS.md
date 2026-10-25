# AGENTS.md — AI Engineering Team & Context Protocol

All AI coding, testing, and on-call agents operating in this repository MUST follow these architectural rules and documentation pointers.

## 1. Modular Documentation Index
- [Product Vision & Privacy Rules](docs/VISION.md)
- [Modular Monolith Architecture & Domain Rules](docs/ARCHITECTURE.md)
- [MVP Scope & Roadmap](docs/ROADMAP_MVP.md)
- [Database Schema Specification](docs/SCHEMA.md) *(to be generated)*
- [REST API Contracts](docs/API.md) *(to be generated)*
- [Implementation Task Backlog](docs/TASKS.md) *(to be generated)*
- [Product Backlog: known problems after the MVP](docs/BACKLOG.md)
- [Deployment, CD & Rollback](docs/DEPLOYMENT.md)

## 2. Agent Team Roles
1. **System Architect Agent:** Maintains `docs/*.md`. Any schema or API change must be documented before implementation.
2. **Core & Backend Agent:** Implements FastAPI modules (`src/core`, `src/modules/*`, `src/plugins/*`). Enforces strict module boundaries and `EventBus` decoupling.
3. **Frontend Agent:** Builds a clean, mobile-first UI supporting the 2-step anomaly confirmation modals, emergency alert banners, and chairman matrix view.
4. **QA & Observability Agent:** Writes unit tests for `policies.py` and integration tests for API routers. Verifies OpenTelemetry `trace_id` propagation and ensures zero PII leakage.
5. **On-Call Responder Agent:** Activated by monitoring webhooks when user-impact alerts fire. Inspects correlated logs by `trace_id`, writes a regression test, patches the bug, and submits a Pull Request.

## 3. Non-Negotiable Guardrails
- **Strict Module Encapsulation:** Never import `models.py` across different modules in `src/modules/`. Communicate exclusively via `service.py` and DTOs in `schemas.py`.
- **Precision:** Use `Decimal` / `Numeric(12, 3)` for all meter values. Never use `float`.
- **Zero PII Leak:** Never return raw `User.full_name`, `email`, or `phone` in `communication` endpoints; always resolve through the privacy badge masker. Never log PII in OpenTelemetry spans or logs.
- **Single-Command Execution:** All changes must keep `docker compose up --build` and `pytest` passing without manual intervention.