# System Architecture: Pragmatic Modular Monolith

## 1. Architectural Pattern
The system is designed as a **Modular Monolith** in Python 3.12+ (FastAPI + SQLAlchemy 2.0 Async + PostgreSQL). It balances strict domain isolation with zero boilerplate.

```text
src/
├── core/                      # Config, Auth/RBAC, Async EventBus, DB Session, OpenTelemetry
├── modules/
│   ├── property/              # [MVP] Building, Entrances, Apartments, Users, Memberships
│   ├── meters/                # [MVP] Meters (Apt & Master), Periods, Readings, Audit Logs
│   ├── communication/         # [MVP] Emergency Alerts, Official Notices, Micro-Forum
│   ├── billing/               # [Phase 2] Tariffs, Parent/Child Cost Allocation, Invoices
│   └── governance/            # [Phase 3] Voting (.asice hash verification), Documents
└── plugins/
    ├── registry.py            # Protocol definitions & dynamic plugin loader
    ├── common/                # Generic plugins: csv_export, telegram_notifier, webhooks
    └── countries/             # Country-specific adapters (ISO 3166-1 alpha-2)
        ├── ee/                # Estonia: e_arve_xml, merit_aktiva, web_eid, asice_verifier
        ├── fi/                # Finland: finvoice
        └── de/                # Germany: datev_export
		
		
		
2. Strict Module Isolation Rules
Vertical Slice Structure per Module:
models.py — Private SQLAlchemy models. NEVER imported outside the module.
schemas.py — Public Pydantic DTOs for API and inter-module contracts.
policies.py — Pure Python domain validation functions (zero DB dependencies, fast unit testing).
service.py — Public facade for routers and other modules.
events.py — Domain events emitted via core.event_bus.
router.py — Thin FastAPI HTTP endpoints.
Database Foreign Keys vs. ORM Relationships:
PostgreSQL enforces foreign keys across tables (e.g., meters.apartment_id -> property_apartments.id).
SQLAlchemy relationship() across module boundaries is FORBIDDEN. Cross-module data is fetched via public Service methods returning DTOs.
In-Memory Async Event Bus:
Side effects (e.g., sending notifications on Emergency Alerts, logging anomalies) are decoupled via event_bus.publish(DomainEvent).