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
│   ├── auth/                  # [Phase 2, E7] Sign-in, sessions, invitations, identities
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

**In-memory async event bus:** Side effects (e.g., sending notifications on emergency alerts, logging anomalies) are decoupled via domain events. Services add them to the request's `EventOutbox`; they are published on the event bus only after the request's transaction commits (see docs/BACKEND_CONVENTIONS.md, section 2.B). Events are also stored in `core_domain_events` in the same transaction (`src/core/domain_events.py`; docs/SCHEMA.md section 6), and a relay publishes any event that a crash or a failing handler left unpublished. Delivery is at least once and unordered, so every subscriber is idempotent and order-independent (details: docs/BACKEND_CONVENTIONS.md 2.B). The table doubles as the event history of the building.

**Plugins** receive plain DTOs defined in `src/plugins/registry.py`, never ORM models, and have **no database access**. Which plugins run is configuration (`ENABLED_PLUGINS`), so a self-hosted instance enables its country adapters without code changes.

## 3. Module `notifications` (epic E1)

Design and the reasoning behind it: [docs/epics/E1_NOTIFICATIONS.md](epics/E1_NOTIFICATIONS.md). Tables: docs/SCHEMA.md section 5. API: docs/API.md section 6.

**Status:** the module, the subscriber, the in-app channel, the feed and preferences exist since task N2 (`src/modules/notifications/`); the dispatcher and the e-mail channel since N3; Telegram linking, the webhook and `common.telegram` since N4; WebPush subscriptions and `common.webpush` since N5. `NotificationChannel`, `OutgoingMessage` and `DeliveryResult` (3.2) are in `src/plugins/registry.py` since N7's stub together with `register_channel` / `channel` / `channels`, and `InboundChannel` / `InboundLink` since N4; the scheduler (3.3) since N6; the FCM plugin (3.2) is planned for N7. Clients poll for in-app notifications; there is no WebSocket (docs/epics/E1_NOTIFICATIONS.md 6.5).

### 3.1 Place in the system
- It **only consumes**: it subscribes to domain events and calls other modules' system-level service methods (3.4). *(Planned, E7:)* it also handles `UserAccountClosed` from `auth` (4.1) by deleting the person's bindings, preferences, link codes and deliveries. No other module imports it, so `NOTIFICATIONS_ENABLED=false` removes it (no subscriber, no loops, no routes) and everything else works unchanged.
- **Per-app event bus.** The bus lives on `app.state.event_bus` (created in `create_app`); `get_event_bus(request)` reads it from there, and the domain event relay uses the same bus. Notifications subscribes in `create_app` on that app's bus, so a test app never inherits handlers of another app. The module registers one database-using handler per event type: in tests all handlers share one session and the bus runs handlers concurrently.
- The subscriber reads its session factory from `app.state.notifications_session_factory` on every event (default: the app's `async_session_factory`); `tests/conftest.py::db_app` points it at the test session. The dispatcher and the scheduler will get theirs the same way.

### 3.2 Channel plugins
New protocols in `src/plugins/registry.py`, next to `ReadingExporter`:

```python
class NotificationChannel(Protocol):
    name: str  # "email", "telegram", "webpush", "mobile_push"

    async def send(self, message: OutgoingMessage) -> DeliveryResult: ...


class InboundChannel(Protocol):  # optional; implemented by common.telegram
    def verify(self, headers: Mapping[str, str]) -> bool: ...  # False -> 404

    def parse_inbound(self, body: bytes) -> InboundLink | None: ...  # None -> ignored, 200

    async def reply(self, chat_id: int, text: str) -> DeliveryResult: ...  # the bot's answer
```

- `InboundLink(chat_id, chat_type, code)`: a `/start <code>` message (`code` is `""` for a bare `/start`); any other update is `None`.
- Registry additions: `register_channel(channel)`, `channel(name) -> NotificationChannel | None`,
  `channels()`; a plugin that also implements `InboundChannel` is found through `channel(name)`.

- `OutgoingMessage` (address, locale, event type, subject, body, deep link, plus `urgent` for emergencies, a `collapse_key` and `expires_at`, used by push channels) is built at send time and never stored or logged; its `repr` hides the address, subject, body and link, and it is never passed to `asdict`/`vars` or into log fields. `DeliveryResult` carries `ok`, `retryable`, `error_code` and `revoke_binding`; no provider text.
- The notifications service owns every table: bindings, link codes, deliveries. Plugins only verify, format and send.
- Plugins read their own settings through `registry.settings` (the app's `Settings`, passed to `build_plugin_registry`); a plugin whose settings are incomplete raises in `register()`, so the app does not start.
- **Message texts** are rendered by the notifications module, never by a plugin: an emergency is the post's title and body as written; every other text is a system text from `src/modules/notifications/messages.csv` (`key,en,ru,et`), picked by the delivery's `locale`, with English when a cell is empty. Placeholders are `{name}` from a fixed set per key and are substituted once (a title containing `{body}` stays literal). The CSV is the only file in the repository that may contain non-English text. Adding a language is one CSV column plus a `Locale` value and a migration of the two `locale` checks.
- Plugins: `common.email_smtp`, `common.telegram`, `common.webpush`, *(N7)* `common.fcm_push` (native phone app through Firebase Cloud Messaging; today a stub that refuses to load). `in_app` is built in.
- **Startup checks.** With any external channel enabled, the app refuses to start without
  `PUBLIC_BASE_URL`; with `common.telegram`, `common.webpush` or `common.fcm_push` also without
  `NOTIFICATIONS_HASH_SECRET` (at least 32 bytes; e-mail hashes nothing). Each plugin also needs its
  own settings: SMTP host and sender; bot token, bot username and a webhook secret of at least
  32 characters in `[A-Za-z0-9_-]`; the VAPID keys and subject; the FCM project id and a readable
  service account file.
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
  2. Send-time checks (`SKIPPED` with an `error_code`): the alert was resolved, the binding is revoked, the user is inactive, the e-mail is gone, a non-mandatory preference was switched off, demo mode, an `EMERGENCY_PUBLISHED` delivery whose row is more than 1 hour old (`EMERGENCY_TOO_OLD`, any external channel: the retry backoff must not bring an alert late). Then the sends run one after another with no transaction open, each cut off after 10 s as a whole (`CHANNEL_TIMEOUT`, retryable; the SMTP client may spend up to 10 s more saying goodbye, so about 20 s at worst; WebPush: no redirects), so a batch of 10 fits in the 5-minute lease. A row whose checks or rendering fail (`PREPARE_ERROR`, retryable) never stops the rest of the batch.
  3. One short transaction per row records `SENT`, `FAILED`, `SKIPPED`, the next retry or a deferral (a reading reminder outside 09:00–21:00 local time: `PENDING` until the next 09:00, the attempt not counted), **fenced**: `UPDATE … WHERE id = :id AND status = 'SENDING' AND attempts = :claimed_attempts`. Zero updated rows means another worker took over after the lease expired, and the result is dropped. Every move out of `SENDING` sets `lease_until = NULL`. A retryable failure on the fifth attempt becomes `FAILED`, never `PENDING`.
  An expired lease is claimed again, so delivery is at least once. Retry backoff: 1 min, 5 min, 30 min, 2 h (5 attempts in total).
- **Scheduler** (`src/modules/notifications/scheduler.py`, every 10 min). A tick has two short transactions, reminders and then clean-up, each under `pg_try_advisory_xact_lock` (a process that does not get the lock skips); a failing stage never blocks the other, and each period runs in its own savepoint, so one broken period never blocks the rest:
  - **reading reminders:** for every open period whose deadline is near (`policies.reminder_due`: from `READING_REMINDER_DAYS_BEFORE` days before the local deadline day, 09:00–21:00 local time, never after `due_date`) that has no `reading_deadline:{period_id}` rows yet, it inserts `READING_DEADLINE` notifications for `reading_submitters(apartments_missing_readings(period))`, through the subscriber's `notify` (preferences, external targets). The rows are the "done" marker, so a period is reminded once, also after a restart; a period without a row (nothing missing, nobody to ask, or every recipient switched reminders off) is checked again on the next tick. The rows are inserted in the tick's transaction rather than through a domain event: the scheduler is part of this module, and the rows and the marker must be one unit. The dispatcher skips a reminder whose `due_date` has passed (`DEADLINE_PASSED`), and one that would go out outside 09:00–21:00 local time (a retry, an outage) goes back to `PENDING` until the next 09:00 without spending an attempt (or is skipped when that is after `due_date`);
  - **clean-up:** deliveries older than `NOTIFICATIONS_RETENTION_DAYS` (also the "published" rows of an emergency still active after that long, so its "resolved" then reaches nobody; accepted, alerts are resolved within days); then bindings revoked before that which no delivery refers to; link codes and link attempts older than 24 hours. Plain `DELETE`s: enough at the scale of a building; batch them and index `binding_id` if volumes grow.

### 3.4 Contracts with other modules
System-level methods (no `CurrentUserDTO`), returning DTOs, used only by notifications:

| Module | Method | Returns |
| :--- | :--- | :--- |
| `property` | `resolve_recipients(building_id, scope, entrance, apartment_ids)` | `list[RecipientDTO(user_id, locale)]`: active users with a membership in a targeted apartment |
| `property` | `users_with_roles(building_id, roles)` | `list[RecipientDTO]` |
| `property` | `has_delivery_email(user_id)` | `bool`, for the channel list of the preferences (the address never leaves) |
| `property` | `reading_submitters(apartment_ids)` | `list[RecipientDTO]`: active members with `can_submit_readings` |
| `property` | `with_delivery_email(user_ids)` | `set[UUID]`: who of them has an e-mail address, for the subscriber (one query per event) |
| `property` | `get_delivery_contact(user_id)` | `DeliveryContactDTO(email)` or `None` (inactive users get `None`) |
| `property` | `is_user_active(user_id)` | `bool` (send-time check) |
| `property` | `get_recipient(user_id)` | `RecipientDTO` of an active user or `None`: who redeems a Telegram link code, and the language of the bot's reply |
| `meters` | `open_periods_due_within(until, *, now)` (status `OPEN_FOR_RESIDENTS`, `now < due_date <= until`) | `list[DuePeriodRefDTO(period_id, building_id, period_code, due_date)]` |
| `meters` | `apartments_missing_readings(period_id)` | `list[UUID]`: apartments with an active meter and no reading |
| `communication` | `get_emergency_ref(post_id, lock_for_share=False)` | `EmergencyRefDTO` regardless of status, or `None`. The subscriber reads it with a share lock and notifies nobody about an alert that is already resolved: events are unordered, and the lock makes a concurrent resolve wait until the "published" rows are stored |

- `EmergencyRefDTO`: `post_id`, `building_id`, `author_user_id`, `is_active`, `target_scope`, `target_entrance`, `target_apartment_ids`, `title`, `body`, `category`. The body is read only by the dispatcher, right before an emergency message is rendered, and never logged.
- **Locale** is resolved when a delivery row is inserted (`RecipientDTO.locale`, else `DEFAULT_LOCALE`) and stored on the row, so the dispatcher needs no extra lookup.
- **Shared targeting rule.** Whether an apartment matches `ALL_BUILDING` / `ENTRANCE` / `APARTMENT_LIST` is one pure predicate. Property uses it for recipients; communication's visibility stays a SQL filter, and tests assert that both select the same apartments.
- **Staff without an apartment** are not recipients through targeting, even for `ALL_BUILDING` (visibility differs on purpose: an accountant without an apartment can read `ALL_BUILDING` posts in the feed). They are notified only through role rules: the chairman and board for emergencies, the chairman and accountants for `PERIOD_REVIEW`.
- **Documented exception to the e-mail rule.** Elsewhere no DTO carries a raw e-mail. `DeliveryContactDTO` exists only for delivery: the dispatcher asks for one user's address right before sending, keeps it in memory for that send, and never logs or stores it. The `property/schemas.py` docstring states this exception (E1/N2).

### 3.5 Settings
| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `NOTIFICATIONS_ENABLED` | `true` | The whole module |
| `NOTIFICATIONS_DISPATCHER_ENABLED` / `NOTIFICATIONS_SCHEDULER_ENABLED` | `true` | The loops |
| `NOTIFICATIONS_RETENTION_DAYS` | `180` | Deletion of old deliveries and of bindings revoked before that |
| `READING_REMINDER_DAYS_BEFORE` | `2` | Reminder lead time; sent between 09:00 and 21:00 local time |
| `INSTANCE_TIMEZONE` | `Europe/Tallinn` | Local time for `due_date` and texts |
| `DEFAULT_LOCALE` | `et` | Language when `core_users.locale` is `NULL` |
| `PUBLIC_BASE_URL` | empty | Base of deep links in external messages; required when an external channel is enabled |
| `NOTIFICATIONS_HASH_SECRET` | empty | Key of the HMAC for chat ids, endpoints, FCM tokens and link codes; required with `common.telegram`, `common.webpush` or `common.fcm_push`. Never rotate it without rehashing: active addresses would lose their uniqueness and open link codes would stop working |
| `SMTP_HOST`, `SMTP_PORT` (`587`), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS` (`starttls`, `tls` or `none`) | | `common.email_smtp`: an SMTP client to an external provider, one account or sender per environment; no mail server in the image (docs/epics/E1_NOTIFICATIONS.md 6.1) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET` | | `common.telegram` (one bot per environment) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | | `common.webpush`: the key pair as base64url (65 and 32 bytes; `python -m src.plugins.common.webpush generate-vapid-keys`, one pair per environment) and a `mailto:` or `https:` contact for the push services. The app refuses to start when the public key does not belong to the private key |
| `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_FILE` | | `common.fcm_push` *(N7)*: the Firebase project and the path of a mounted service account key file (never the key itself in the environment) |

### 3.6 Privacy and observability
No names, e-mails, phones, chat ids, push endpoints and their keys, FCM tokens, link codes, bot tokens or message bodies in logs or spans (httpx request logs are silenced because Bot API URLs carry the token, and the scrubber masks token patterns). Spans carry `event_type`, `channel`, `status`, `delivery_id`; the send span `notifications.deliver` joins the trace of the request that caused the event (`notif_deliveries.trace_id`). Metrics: `notifications.deliveries` (counter by `channel` and `status`: sent, failed, skipped, retry), `notifications.revoked_bindings`, `notifications.reminded_periods` and `notifications.deleted_rows` (by `table`).

## 4. Module `auth` (epic E7) *(in progress)*

Design: [docs/epics/E7_LOGIN.md](epics/E7_LOGIN.md). Tables: docs/SCHEMA.md section 7. API: docs/API.md section 7. Contract of task A1; implemented by A2-A5. **Status:** A2 is live: `src/modules/auth/` with sessions, refresh tokens, the session check in `get_current_user`, the startup checks of 4.7 and the hourly clean-up of ended sessions. A3-A5 add sign-in, invitations and passwords.

### 4.1 Place in the system
- **Tables:** `src/modules/auth/` owns `core_user_identities` (moved from `property`), `auth_sessions`, `auth_refresh_tokens`, `auth_invitations`, `auth_tokens` and `auth_attempts`. `property` keeps users, apartments and memberships.
- **Dependencies point one way:**
  - `auth` reads and changes users and memberships only through the `PropertyService` system methods of 4.2, never through `property` models;
  - `auth` sends e-mail through the plugin registry (4.5);
  - `property` never imports `auth`;
  - `src/core/security.py` asks `auth` whether a session is active (4.4), the same way it already asks `property` for the current user.
- **Events:** `auth/events.py` defines `UserAccountClosed(user_id, building_id)`, ids only, stored durably in the transaction that closes the account. `src.modules.auth.events` is added to `_MODULE_EVENT_MODULES` in `src/core/events_registry.py`. Notifications subscribes to it and deletes the person's bindings, preferences, link codes and deliveries.
  - **A backstop, because the event reaches nobody while `NOTIFICATIONS_ENABLED=false`:** the notifications clean-up (3.3) asks `PropertyService.inactive_user_ids` about the users it still has rows for, and deletes those rows of closed users. Turning notifications on later leaves nothing behind.
- **Routers:** `/api/v1/auth/...`, including the chairman's user administration (roles, memberships, closing).

### 4.2 Contracts with `property` (system-level, no `CurrentUserDTO`)
| Method | Returns / does |
| :--- | :--- |
| `find_active_user_by_email(email)` | `UserRefDTO(user_id, building_id, building_role)` or `None`; canonical match |
| `user_ref(user_id)` | `UserRefDTO` of an active user, or `None` (closed or unknown) |
| `create_user(building_id, email, full_name, phone, locale, building_role)` | the new user id (registration) |
| `open_membership(user_id, apartment_id, relation_type, privacy_display_mode)` | Creates the membership, or reopens an ended one (`ended_at = NULL`, the given relation and mode, `can_vote` only for `OWNER`). Returns `False` without any change when an active membership already exists |
| `membership_ref(membership_id)` / `active_membership(user_id, apartment_id)` | `MembershipRefDTO(membership_id, user_id, apartment_id, building_id, relation_type)` of an active membership, or `None` |
| `end_membership(membership_id)` | Sets `ended_at`; returns whether the user still has an active membership or a staff role |
| `set_building_role(user_id, role)` | Refuses to leave a building without an active chairman (`LastChairmanError`). Locks the building's active `CHAIRMAN` rows (`FOR UPDATE`) before counting, so two concurrent step-downs cannot both pass |
| `close_account(user_id)` | Ends all memberships; `is_active = false`; e-mail and phone `NULL`, name `"Former resident"`. Refuses the last active chairman, with the same lock |
| `apartment_by_number(building_id, number)` / `apartment_ref(apartment_id)` | `ApartmentRefDTO(apartment_id, building_id, number)` or `None` |
| `lock_apartment(apartment_id)` | `SELECT … FOR UPDATE` on the apartment row, held for the transaction, so the tenant limit cannot be exceeded by concurrent invitations |
| `is_owner_of(user_id, apartment_id)` | active `OWNER` membership of an active user |
| `active_tenant_count(apartment_id)` | active `TENANT` memberships of active users |
| `apartment_residents(apartment_id)` | `list[ResidentDTO(user_id, membership_id, relation_type, display_label, since)]`, badge rules of API section 0 |
| `membership_end_times(membership_ids)` | `dict[UUID, datetime \| None]`: when each membership ended (for the retention of invitations) |
| `inactive_user_ids(user_ids)` | which of these users are closed (for the notifications backstop, 4.1) |
| `has_active_owner(apartment_id)` | whether a `TENANT` invitation still applies |
| `active_chairman_count(building_id)` | active users with `CHAIRMAN` (`is_active` filtered; a closed account keeps its old `building_role`) |

### 4.3 Which rows are "active"
Every query that resolves people filters closed accounts (`core_users.is_active`) and ended memberships (`property_memberships.ended_at IS NULL`). Task A4 adds the missing filters and a test per row:

| Query (`PropertyService`, today) | `is_active` | `ended_at IS NULL` |
| :--- | :--- | :--- |
| `get_current_user` and `_memberships_of` (the source of every permission check) | yes (today) | add |
| `get_me`, `update_locale` | through `get_current_user` | add (via `_memberships_of`) |
| `update_privacy_mode` (own membership) | through `get_current_user` | add: an ended membership is `404 MEMBERSHIP_NOT_FOUND` |
| `resolve_recipients` (emergency targeting) | yes (today) | add |
| `users_with_roles` | yes (today) | n/a (no memberships) |
| `reading_submitters` | yes (today) | add |
| `has_delivery_email`, `with_delivery_email` | add | n/a |
| `get_delivery_contact`, `get_recipient`, `is_user_active` | yes (today) | n/a |
| `get_masked_author_badges` | no (history: closed authors keep a masked badge) | add: an ended membership counts as "no longer a member" |
| `list_apartments`, `get_building` | n/a | n/a |
| demo user list (`src/core/demo.py`) | yes (today); apartment and relation come from the static `DEMO_USERS`, not from live memberships | n/a |

### 4.4 Sessions in `get_current_user`
- **A Bearer token with `sid`** is accepted only while that session is active and belongs to `sub` (one indexed lookup).
- **`auth_time`** is the input of the "recent sign-in" check (10 minutes) for the password endpoints. A token without `auth_time` never counts as recent.
- **A token without `sid`,** minted by `create_access_token` in tests, is accepted only with `AUTH_ALLOW_SESSIONLESS_TOKENS=true`. That is false by default; the test settings set it. The app never issues one.
- **The demo header** works as today for the rest of the API, and is refused by all of API section 7.

### 4.5 E-mails of `auth`
- **Sender:** the e-mail plugin (`registry.channel("email")`, `common.email_smtp`) directly, not through notifications. Login must not depend on `NOTIFICATIONS_ENABLED`, and a token must never be stored in a delivery row.
- **When:** after the commit, in a background task of the request (FastAPI `BackgroundTasks`, which the app already uses for the Telegram reply).
  - The plain token lives only in memory until then.
  - A failed send is logged with the error code only; the person asks again, or the inviter resends.
- **Texts:** en, ru and et in `src/modules/auth/messages.csv`, with the catalog format of E1.
  - The catalog loader moves from `src/modules/notifications/messages.py` to `src/core/messages.py`.
  - The repository language guard (`tests/core/test_repository_language.py`) is extended to allow Cyrillic in both CSV files.
- These e-mails are transactional and carry no unsubscribe link (E1/N8).
- Without the e-mail plugin nothing is sent, so the sign-in link cannot work; the operator command warns about it (API section 7).

### 4.6 Demo mode
- Every endpoint of API section 7 works in demo mode (owner decision: demo mode stays on everywhere for now). They refuse the demo header, so account pages need a real sign-in.
- **E-mails in demo mode** go out only with `AUTH_MAIL_IN_DEMO=true`; otherwise the endpoints answer as usual and send nothing.
  - The app refuses to start with `AUTH_MAIL_IN_DEMO=true` unless `ENVIRONMENT` is `local`, `dev` or `test`, an allowlist, so an unknown name fails closed.
  - Dev may set it only once it is behind a password gate (Caddy `basic_auth`, docs/DEPLOYMENT.md), a precondition of A3. Locally, Mailpit catches everything.
  - Optionally, `AUTH_DEMO_MAIL_DOMAINS` (comma-separated) restricts the recipients in demo mode.
- **Leaving demo mode later** (an owner decision; docs/epics/E7_LOGIN.md 3.8):
  1. rotate `JWT_SECRET_KEY` and `AUTH_HASH_SECRET`;
  2. delete all password identities;
  3. review the accounts and invitations created during demo mode;
  4. configure a real SMTP sender.

### 4.7 Settings
| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `JWT_SECRET_KEY` | empty | Now required, at least 32 bytes |
| `AUTH_HASH_SECRET` (`SecretStr`) | empty | Key of every `auth` hash; required, at least 32 bytes. Rotating it ends every session and voids every open link and invitation |
| `JWT_ACCESS_TOKEN_TTL_MINUTES` | `15` (was `60`) | Lifetime of access tokens |
| `AUTH_SESSION_IDLE_DAYS` / `AUTH_SESSION_MAX_DAYS` | `7` / `90` | Refresh cookie lifetime (extended on use), and the hard cap of a session |
| `MAX_TENANTS_PER_APARTMENT` | `3` | Active tenants plus pending tenant invitations per apartment |
| `AUTH_MAIL_IN_DEMO` | `false` | Send login e-mails while demo mode is on (4.6) |
| `AUTH_DEMO_MAIL_DOMAINS` | empty | Optional recipient domains allowed in demo mode |
| `AUTH_ALLOW_SESSIONLESS_TOKENS` | `false` | Accept tokens without `sid` (tests only, 4.4); refused at startup unless `ENVIRONMENT` is `local`, `dev` or `test` |
| `AUTH_CLEANUP_ENABLED` | `true` | The hourly clean-up loop (docs/SCHEMA.md 7, retention) |

- **Where the secrets are checked:** in `create_app` / the app startup, not by a field validator. `Settings()` is built at import time, and `python -m src.core.openapi`, migrations, the seed and the CLI must keep working without a `.env`.
- **Values:** `docker-compose.yml` and the test settings get local-only values; each deployed environment gets its own in `.env` (docs/DEPLOYMENT.md). `tests/core/test_security.py` changes accordingly (A2).

### 4.8 Privacy, security and observability
- **Never in logs, spans or metrics:** e-mails, tokens, passwords, cookies, IP addresses, the raw `User-Agent`. Only keyed hashes are stored.
- **New scrubber markers:** `refresh`, `magic`, `client_ip`, `ip_address`, `user_agent`. It already has `token`, `email`, `password`, `secret`, `cookie` and `authorization`.
- **Spans** carry `auth.method`, `auth.outcome` (`signed_in`, `failed`, `throttled`) and the session id.
- **Metrics:** `auth.sign_ins` (by method and outcome) and `auth.revoked_sessions` (by reason).
- **Hashing:** Argon2id runs in a thread pool, at most 2 at a time, so a burst of logins cannot use up the memory of a small server.
- **Response headers:** `src/core/frontend.py` (the SPA) and the API responses send `Referrer-Policy: no-referrer`, so a link page never leaks its URL.
- **Client IP:** the per-IP limits use the address uvicorn takes from `X-Forwarded-For` (`--proxy-headers`). Caddy sets that header, and the backend port is published only to Caddy, never to the internet.
- **Logs:** uvicorn runs without an access log. The reverse proxy keeps its access log off, or excludes `Cookie` and `Authorization`.
