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
│   ├── notifications/         # [Phase 2, E1] Deliveries, preferences, channel bindings
│   ├── billing/               # [Phase 2, E2] Tariffs, Parent/Child Cost Allocation, Charges
│   └── governance/            # [Phase 3, E3] Voting (.asice hash verification), Documents
└── plugins/                   # Plugin names are dotted paths: "common.csv_export"
    ├── registry.py            # Protocol definitions & dynamic plugin loader
    ├── common/                # Generic: csv_export, email_smtp, telegram, webpush
    └── countries/             # Country-specific adapters (ISO 3166-1 alpha-2)
        ├── ee/                # Estonia: e_arve_xml, merit, web_eid, asice_verifier
        ├── fi/                # Finland: finvoice
        └── de/                # Germany: datev_export
```

## 2. Strict Module Isolation Rules

**Vertical slice structure per module:**
- `models.py` — Private SQLAlchemy models. NEVER imported outside the module.
- `schemas.py` — Public Pydantic DTOs for API and inter-module contracts.
- `policies.py` — Pure Python domain validation functions (zero DB dependencies, fast unit testing).
- `service.py` — Public facade for routers and other modules.
- `events.py` — Domain events published on the event bus.
- `router.py` — Thin FastAPI HTTP endpoints.

**Database foreign keys vs. ORM relationships:** PostgreSQL enforces foreign keys across tables (e.g., `meters.apartment_id -> property_apartments.id`). SQLAlchemy `relationship()` across module boundaries is FORBIDDEN. Cross-module data is fetched via public Service methods returning DTOs.

**In-memory async event bus:** Side effects (e.g., sending notifications on emergency alerts, logging anomalies) are decoupled via domain events. Services add them to the request's `EventOutbox`; they are published on the event bus only after the request's transaction commits (see docs/BACKEND_CONVENTIONS.md, section 2.B). Events live only in memory: a process that dies right after a commit loses them, so a consumer that must not miss an event reconciles from the source of truth (see section 3.3).

**Plugins** receive plain DTOs defined in `src/plugins/registry.py`, never ORM models, and have **no database access**. Which plugins run is configuration (`ENABLED_PLUGINS`), so a self-hosted instance enables its country adapters without code changes.

## 3. Module `notifications` (planned, epic E1)

Design and the reasoning behind it: [docs/epics/E1_NOTIFICATIONS.md](epics/E1_NOTIFICATIONS.md). Tables: docs/SCHEMA.md section 5. API: docs/API.md section 6.

### 3.1 Place in the system
- It **only consumes**: it subscribes to domain events and calls other modules' system-level service methods (3.4). No other module imports it, so `NOTIFICATIONS_ENABLED=false` removes it (no subscriber, no loops, no routes) and everything else works unchanged.
- **Per-app event bus (E1/N2).** The bus moves from the process-global `event_bus` to `app.state.event_bus`; `get_event_bus(request)` reads it from there. Notifications subscribes in `create_app` on that app's bus, so a test app never inherits handlers of another app. The module registers one database-using handler per event type: in tests all handlers share one session and the bus runs handlers concurrently.
- The subscriber, the dispatcher and the scheduler receive a session factory and the plugin registry from `create_app`, not the request-scoped dependencies; tests override both.

### 3.2 Channel plugins
New protocols in `src/plugins/registry.py`, next to `ReadingExporter`:

```python
class NotificationChannel(Protocol):
    name: str  # "email", "telegram", "webpush"

    async def send(self, message: OutgoingMessage) -> DeliveryResult: ...


class InboundChannel(Protocol):  # optional; implemented by common.telegram
    def verify(self, headers: Mapping[str, str]) -> bool: ...  # False -> 404

    def parse_inbound(self, body: bytes) -> InboundLink | None: ...  # None -> ignored, 200
```

- `InboundLink(chat_id, chat_type, code)`: a `/start <code>` message; any other update is `None`.
- Registry additions: `register_channel(channel)`, `channel(name) -> NotificationChannel | None`,
  `channels()`; a plugin that also implements `InboundChannel` is found through `channel(name)`.

- `OutgoingMessage` (address, locale, subject, body, deep link) is built at send time and never stored or logged. `DeliveryResult` carries `ok`, `retryable`, `error_code` and `revoke_binding`; no provider text.
- The notifications service owns every table: bindings, link codes, deliveries. Plugins only verify, format and send.
- Plugins: `common.email_smtp`, `common.telegram`, `common.webpush`. `in_app` is built in.
- **Startup checks.** With any external channel enabled, the app refuses to start without
  `PUBLIC_BASE_URL`; with `common.telegram` or `common.webpush` also without
  `NOTIFICATIONS_HASH_SECRET` (at least 32 bytes; e-mail hashes nothing). Each plugin also needs its
  own settings: SMTP host and sender; bot token, bot username and a webhook secret of at least
  32 characters in `[A-Za-z0-9_-]`; the VAPID keys and subject.
- **Demo mode (owner decision).** While `DEMO_MODE_ENABLED=true` (anyone can act as anyone),
  external channels are treated as unavailable: no linking, and the dispatcher never calls an
  external plugin. Only `in_app` works. There is no override. Every environment today (docker
  compose, dev, prod) runs in demo mode and keeps the external plugins disabled, so the startup
  checks never fire there. E-mail, Telegram and WebPush are verified by automated tests that run
  with `DEMO_MODE_ENABLED=false` and a test JWT (`JWT_SECRET_KEY` set in the test). Using them for
  real needs a real login first (docs/BACKLOG.md B-7).

### 3.3 Background loops
Both start in the app lifespan, stop on shutdown, and have switches (`NOTIFICATIONS_DISPATCHER_ENABLED`, `NOTIFICATIONS_SCHEDULER_ENABLED`); tests turn them off and call one tick directly.
- **Dispatcher** (every 5 s), lease protocol:
  1. A short transaction claims up to 10 rows: `(status = 'PENDING' AND next_attempt_at <= now()) OR (status = 'SENDING' AND lease_until < now())`, both with `attempts < 5`, `ORDER BY priority, next_attempt_at FOR UPDATE SKIP LOCKED`. It sets `SENDING`, `lease_until = now() + 5 min`, `attempts = attempts + 1`. In the same transaction, expired `SENDING` rows with `attempts = 5` become `FAILED` (`LEASE_EXPIRED`, `lease_until = NULL`), also selected with `SKIP LOCKED`.
  2. Send-time checks (`SKIPPED` with an `error_code`): the alert was resolved, the binding is revoked, the user is inactive, the e-mail is gone, a non-mandatory preference was switched off, demo mode. Then the sends run one after another with no transaction open, each with a 10 s timeout (WebPush: no redirects), so a batch fits in the lease.
  3. A second transaction records `SENT`, `FAILED`, `SKIPPED` or the next retry, **fenced**: `UPDATE … WHERE id = :id AND status = 'SENDING' AND attempts = :claimed_attempts`. Zero updated rows means another worker took over after the lease expired, and the result is dropped. Every move out of `SENDING` sets `lease_until = NULL`. A retryable failure on the fifth attempt becomes `FAILED`, never `PENDING`.
  An expired lease is claimed again, so delivery is at least once. Retry backoff: 1 min, 5 min, 30 min, 2 h (5 attempts in total).
- **Scheduler** (every 10 min), each tick in a short transaction under `pg_try_advisory_xact_lock`:
  - **repairs lost emergency events:** for active emergencies **created less than 30 minutes ago**, it creates the missing deliveries, only for recipients created before the alert (`RecipientDTO.created_at`) who have no row at all for it, and only on bindings created before it. Older alerts, the seeded alert, and alerts already active (and older than 30 minutes) when the module is first deployed are never sent late; retention cannot re-trigger them either. `RecipientDTO.created_at` is `core_users.created_at` (memberships have no timestamp), so an existing user who joins an apartment inside the window can still get the repaired alert, which is accepted. **One tick runs at startup**, so the guarantee is: a lost emergency event is repaired if the app runs within 30 minutes of the alert; after a longer outage only the banner shows it;
  - publishes reading reminders inside the 09:00–21:00 local window;
  - deletes deliveries older than the retention period, and link codes and link attempts older than 24 hours.

### 3.4 Contracts with other modules
System-level methods (no `CurrentUserDTO`), returning DTOs, used only by notifications:

| Module | Method | Returns |
| :--- | :--- | :--- |
| `property` | `resolve_recipients(building_id, scope, entrance, apartment_ids)` | `list[RecipientDTO(user_id, locale, created_at)]`: active users with a membership in a targeted apartment |
| `property` | `users_with_roles(building_id, roles)` | `list[RecipientDTO]` |
| `property` | `reading_submitters(apartment_ids)` | `list[RecipientDTO]`: active members with `can_submit_readings` |
| `property` | `get_delivery_contact(user_id)` | `DeliveryContactDTO(email)` or `None` |
| `property` | `is_user_active(user_id)` | `bool` (send-time check) |
| `meters` | `open_periods_due_within(until)` | `list[DuePeriodRefDTO(period_id, building_id, period_code, due_date)]` |
| `meters` | `apartments_missing_readings(period_id)` | `list[UUID]`: apartments with an active meter and no reading |
| `communication` | `active_emergencies()` | `list[EmergencyRefDTO]` across all buildings |
| `communication` | `get_emergency_ref(post_id)` | `EmergencyRefDTO` regardless of status, or `None` |

- `EmergencyRefDTO`: `post_id`, `building_id`, `author_user_id`, `is_active`, `created_at`, `target_scope`, `target_entrance`, `target_apartment_ids`, `title`, `category`.
- **Locale** is resolved when a delivery row is inserted (`RecipientDTO.locale`, else `DEFAULT_LOCALE`) and stored on the row, so the dispatcher needs no extra lookup.
- **Shared targeting rule.** Whether an apartment matches `ALL_BUILDING` / `ENTRANCE` / `APARTMENT_LIST` is one pure predicate. Property uses it for recipients; communication's visibility stays a SQL filter, and tests assert that both select the same apartments.
- **Staff without an apartment** are not recipients through targeting, even for `ALL_BUILDING` (visibility differs on purpose: an accountant without an apartment can read `ALL_BUILDING` posts in the feed). They are notified only through role rules: the chairman and board for emergencies, the chairman and accountants for `PERIOD_REVIEW`.
- **Documented exception to the e-mail rule.** Elsewhere no DTO carries a raw e-mail. `DeliveryContactDTO` exists only for delivery: the dispatcher asks for one user's address right before sending, keeps it in memory for that send, and never logs or stores it. The `property/schemas.py` docstring states this exception (E1/N2).

### 3.5 Settings
| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `NOTIFICATIONS_ENABLED` | `true` | The whole module |
| `NOTIFICATIONS_DISPATCHER_ENABLED` / `NOTIFICATIONS_SCHEDULER_ENABLED` | `true` | The loops |
| `NOTIFICATIONS_RETENTION_DAYS` | `180` | Deletion of old deliveries |
| `READING_REMINDER_DAYS_BEFORE` | `2` | Reminder lead time; sent between 09:00 and 21:00 local time |
| `INSTANCE_TIMEZONE` | `Europe/Tallinn` | Local time for `due_date` and texts |
| `DEFAULT_LOCALE` | `et` | Language when `core_users.locale` is `NULL` |
| `PUBLIC_BASE_URL` | empty | Base of deep links in external messages; required when an external channel is enabled |
| `NOTIFICATIONS_HASH_SECRET` | empty | Key of the HMAC for chat ids, endpoints and link codes; required with `common.telegram` or `common.webpush`. Never rotate it without rehashing: active addresses would lose their uniqueness and open link codes would stop working |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` | | `common.email_smtp` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET` | | `common.telegram` (one bot per environment) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | | `common.webpush` |

### 3.6 Privacy and observability
No names, e-mails, phones, chat ids, endpoints or message bodies in logs or spans. Spans carry `event_type`, `channel`, `status`, `delivery_id`. Metrics: deliveries by channel and status, retries, revoked bindings.
