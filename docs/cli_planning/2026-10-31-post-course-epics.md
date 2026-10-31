# 2026-10-31: Post-course planning (epics and workflow)

A record of a planning session with the coding agent (Claude Code CLI) after the course
submission. It explains why the epics in [docs/epics/](../epics/README.md) look the way they do.

## 1. Input: what associations expect from such software

A review of the apartment association software market (Estonia) showed where mature products
spend their effort:

1. **Accounting and finance** dominate: ledger, bank integrations, VAT, annual reports, tax forms.
2. **Payment reminders and debt handling** are among the most used features.
3. **Voting without a meeting** keeps getting legal details: proxies, legal-entity owners, board
   decisions, abstain, ties, protocols.
4. **Remote meter reading**: a public API for meter vendors, and lately hourly or 15-minute
   electricity readings for exchange-price billing.
5. **Bulk setup**: importing apartments and owners instead of typing them in.
6. **AI for routine work**: recognising documents and photos.

Where this project stands apart:

| Topic | This project |
|---|---|
| Model | Open source, self-hosted, one instance per association |
| Countries | Country-agnostic core plus country plugins |
| Privacy | Backend author masking, zero PII in logs |
| Readings | Two-step anomaly confirmation, rolling average, period state machine, audit log |
| Emergencies | Targeted emergency banner with acknowledge/resolve |
| Operations | OpenTelemetry, alerting, on-call agent, CD with rollback |

## 2. Questions answered in the session

**Proxy voting.** An owner authorises another person to vote for them; a legal-entity owner votes
through a representative. Voting is abstract in the core, and verified (signed) voting is a
country plugin; for Estonia, a signed `.asice` container with the ballot PDF. Details: E3.

**Several readings per month.** Already supported, no change: `UNIQUE(meter_id, period_id)` keeps
one reading per meter and period, a new submission overwrites the value, and every change writes
an audit row to `meter_reading_logs`. Hourly smart-meter readings are a different feature
(docs/BACKLOG.md B-5).

**Accounting.** A full accounting system is years of work and not our strength. We calculate
charges; accounting software handles money. Export through country plugins (e-invoice XML, Merit
Aktiva). Details: E2.

**Notifications.** A separate module with channel plugins rather than per-feature reminders.
Details: E1.

## 3. Decisions

- **main is frozen** while the course submission is under peer review. All work goes to the
  `develop` branch through pull requests; after the review `develop` is merged into `main` and the
  repository is renamed.
- Epics are tracked as a page in `docs/epics/` plus a GitHub issue with the `epic` label;
  milestones `Phase 2` and `Phase 3`; one issue, branch and PR per task.
- The first epic is E1 Notifications.
- **Mandatory CTO review:** when a task is done and `pytest` is green, a fresh reviewer agent
  without the development context reviews only the branch commits and the current code (quality,
  security, bugs, AGENTS.md guardrails). The implementing agent verifies each finding, fixes the
  confirmed ones in the same branch, and lists the findings in the PR before the user approves the
  merge.
