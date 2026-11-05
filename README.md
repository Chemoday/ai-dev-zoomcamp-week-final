# Apartment Association Platform

[![CI](https://github.com/Chemoday/apartment-association-platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Chemoday/apartment-association-platform/actions/workflows/ci.yml)

A web app for an apartment association (in Estonia a *korteriühistu*): residents submit their
meter readings, the board checks them in one place, emergencies reach the right entrance, and
neighbours, the board and each apartment talk to each other without exposing names or phone
numbers unless a person chooses to.

One installation serves one association, on a small server the association controls. The code is
public so that anyone can check its privacy promises, and it is free forever for an association
that runs it itself ([license](#license)).

**Live demo:** https://dev.164-68-121-228.sslip.io:8443 (pick a demo role on the start screen) ·
**API docs:** https://dev.164-68-121-228.sslip.io:8443/docs

---

## Why

Most associations are run by volunteers: a chairman, a few board members and an accountant. Every
month each apartment reports its water readings, and the board turns them into bills. Usually this
happens in a messenger chat and a spreadsheet: readings arrive late or with typos, the chairman
chases the missing apartments by hand, the basement master meter never matches the sum of the
flats, an outage notice goes to everybody or to nobody, and the chat shows everyone's name and
phone number to all neighbours. Commercial tools exist, but they are costly for a small building
and keep the association's data on someone else's server.

## What it does

**Meter readings**
- Residents see their meters, last period's value and their usual consumption, and submit the
  monthly readings. A reading below the previous one is rejected; an unusually large one needs two
  explicit confirmations before it is saved.
- The board sees all apartments of a period in one matrix (submitted, missing, estimated, confirmed
  anomaly), the master meters and the water loss; it estimates missing readings, corrects a reading
  with a mandatory reason and exports a CSV for the accountant.
- Values are exact decimals end to end, and every change is kept in an audit trail.

**Communication**
- Emergencies and official notices for the whole building, one entrance or chosen apartments.
- A forum for neighbours. Authors appear as a badge such as `Apt. 14 • OWNER`; whether a name is
  shown is each resident's own choice.
- Private conversations between the board and one apartment: every owner and tenant of that
  apartment sees them, a new tenant only from the day they moved in.
- A micro-forum of the board for handover notes, keys and contractors.

**For the board**
- Apartment cards: the apartments with their residents as "Owner since ..." (never names or
  contacts) and the readings across periods.

**Notifications**
- In the app, by e-mail, in Telegram and as browser push, with per-person settings, reading
  reminders before the deadline and one-click unsubscribe. Messages never carry what a neighbour
  wrote, only that there is something new.

**Accounts**
- Sign-in by e-mail link or an optional password, invitations by the board and by owners for their
  tenants, sessions per device, closing an account with the removal of personal data.

## Try the demo

The demo building **KÜ Sõpruse 12** (6 apartments in 2 entrances) is seeded automatically. Pick a
role on the start screen:

1. **Owner Apt 14**: submit October's cold water reading. A normal value is about 4.8 m³ above the
   previous one; try a much larger value to see both confirmation popups.
2. **Tenant Apt 2**: entrance 1, so the hot water emergency for entrance 2 is not shown; the forum
   shows this tenant as `M. T. (Apt. 2)` because of the chosen privacy mode.
3. **Chairman**: the readings matrix, the master meter balance, the period status, estimates and
   the CSV export.

The demo data is shared by all visitors. The demo runs in demo mode: anyone can act as any demo
user, and nothing is sent outside the app.

## Run it yourself

Requirements: Docker with Compose.

```bash
docker compose up --build
```

| URL | What |
| :--- | :--- |
| http://localhost:8000 | The app (the backend serves the built client) |
| http://localhost:8000/docs | API documentation (OpenAPI) |
| http://localhost:3000 | Grafana: traces, logs, metrics and alerts (`admin` / `admin`) |

This starts PostgreSQL, the backend (migrations and demo data run on start), the OpenTelemetry
collector and Grafana. A server installation with HTTPS, automatic deploys and rollback is
described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and Docker (the tests start a throwaway
PostgreSQL container).

```bash
uv sync
uv run pytest        # unit and integration tests
uv run ruff check .
```

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16; a modular
  monolith (`property`, `meters`, `communication`, `notifications`, `auth`) whose modules talk only
  through services and DTOs, with side effects through a durable event outbox
  ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).
- **Client:** Vue 3, Vite, Tailwind, Pinia, vue-i18n, in `frontend/` (synced from the frontend
  repository, [docs/FRONTEND_GIT_WORKFLOW.md](docs/FRONTEND_GIT_WORKFLOW.md)).
- **Ops:** Docker Compose, GitHub Actions, images in GHCR, Caddy on a VPS, OpenTelemetry and
  Grafana, an alert on failed reading submissions and an on-call agent that proposes a fix
  ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)).
- **How work is done:** every change is specified in `docs/` first, built on its own branch and
  reviewed before the merge; AI agents and people follow the same rules ([AGENTS.md](AGENTS.md)).

## Documentation

| Document | Contents |
| :--- | :--- |
| [docs/VISION.md](docs/VISION.md) | Product vision, privacy principles, role model |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Modular monolith, module rules, event bus, plugins |
| [docs/API.md](docs/API.md), [docs/openapi.json](docs/openapi.json) | REST API rules and the generated contract |
| [docs/SCHEMA.md](docs/SCHEMA.md) | Database schema |
| [docs/epics/](docs/epics/README.md) | Designs of the features after the MVP |
| [docs/FRONTEND_GUIDE.md](docs/FRONTEND_GUIDE.md) | What the client must get right (decimals, popups, privacy) |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | VPS, CI/CD, dev and prod, rollback |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Telemetry, the alert and the on-call agent |
| [docs/BACKLOG.md](docs/BACKLOG.md) | Known problems and ideas for later |
| [AGENTS.md](AGENTS.md) | Rules and the documentation index for AI agents and contributors |
| [docs/COURSE_PROJECT.md](docs/COURSE_PROJECT.md) | Where it started: the final project of the AI Dev Tools Zoomcamp 2026 |

## License

Source-available under the [Elastic License 2.0 with additional permissions](LICENSE):

- **Free forever for an apartment association that runs it itself**, on a server or cloud
  account the association controls (it is the account holder and has full admin access). Anyone
  (a resident, a friend, an IT contractor) may set it up and maintain it there, paid or not
  (ongoing paid support for more than one association needs a license, see below).
- A member, owner or resident may host each association where they own or live in a flat or
  house, for no more than the actual server cost (board member pay that is not for hosting does
  not count); a property management company may host one association it manages.
- **Needs a written license:** hosting installations on your own infrastructure for associations
  other than your own, a recurring service for this software (updates, support plans) sold to
  more than one association, and offering it as a service or managed cloud. Regional partner
  licenses are possible on request.

## Contributing

Pull requests, translations and country modules are welcome under the contributor license
agreement in [CONTRIBUTING.md](CONTRIBUTING.md).
