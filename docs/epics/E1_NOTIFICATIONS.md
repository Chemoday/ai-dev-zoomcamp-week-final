# E1. Notifications

- **Phase:** 2. **Status:** design. **Epic issue:** [#39](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/39).
- **Goal:** one pluggable notification system that every module uses, instead of each feature
  sending its own messages. Reminders, emergency alerts and (later) voting all go through it.

This page is the design. Task N1 turns it into the binding contract (docs/SCHEMA.md, docs/API.md,
docs/openapi.json, docs/ARCHITECTURE.md); where they differ, the contract wins.

## 1. Scope

In scope:
- A new module `src/modules/notifications/` (the standard vertical slice: `models.py`,
  `schemas.py`, `policies.py`, `service.py`, `router.py`, `events.py`).
- A built-in `in_app` channel (a notification feed in the UI).
- Delivery channels as plugins: `common.email_smtp`, `common.telegram`, `common.webpush`.
- Per-user preferences (event type x channel).
- Background loops: a dispatcher (sends and retries) and a scheduler (reminders, reconciliation).

Out of scope (later epics or backlog):
- Debt reminders: need payment data, see docs/BACKLOG.md B-6.
- SMS (a paid provider; can be one more channel plugin later).
- Quiet hours (not in this epic).

## 2. How it works

```text
business transaction commits
  -> EventOutbox publishes the domain event (in memory)
  -> notifications subscriber: resolve recipients, apply preferences,
     INSERT notification_deliveries (PENDING) -- never sends
  -> dispatcher loop (every few seconds): claim due rows with FOR UPDATE SKIP LOCKED,
     render the message, call the channel plugin, set SENT / FAILED / next_attempt_at
```

The subscriber only inserts rows, so a large building never slows down the request that
published the event (handlers run before the response, docs/BACKEND_CONVENTIONS.md 2.B).

The module only subscribes to events and exposes its own API. Other modules never import it, so it
can be switched off (`NOTIFICATIONS_ENABLED=false`) and the rest of the system works unchanged.

**Section 10 records decisions made after review; where an earlier section is less precise,
section 10 wins.**

### 2.1 Events handled

| Event | Source | Recipients | Mandatory | `dedup_key` |
|---|---|---|---|---|
| `EmergencyAlertPublished` | communication | users in the alert target plus chairman and board, once per user (10.3) | yes | `emergency:{post_id}` |
| `EmergencyAlertResolved` | communication | users whose `emergency:{post_id}` delivery was SENT (10.5) | no | `emergency_resolved:{post_id}` |
| `ReadingDeadlineApproaching` (new, from the scheduler) | notifications | members with `can_submit_readings` of apartments missing a reading | no | `reading_deadline:{period_id}` |
| `ReadingPeriodStatusChanged` -> `REVIEW_BY_BOARD` | meters | chairman and accountants | no | `period_review:{period_id}:{n}`, n = how many times the period entered review |

- The user who caused an event (`published_by_user_id`, `changed_by_user_id`, ...) is not notified
  about their own action.
- "Missing a reading": at least one active apartment meter of the apartment has no reading in the
  period.

### 2.2 Reliability: what happens if the process dies

Domain events live only in memory and are published after commit. If the process dies between the
commit and the subscriber's insert, the event is lost. The accepted design:
- **Mandatory events are reconciled.** Every scheduler tick asks communication for active
  emergencies and creates the missing deliveries (exact rule in 10.5). The `dedup_key` makes this idempotent. A
  lost emergency notification is therefore delayed by at most one tick, never lost.
- **Other events are best effort.** A lost "period in review" or "resolved" notification is an
  accepted risk. Reading reminders are produced by the scheduler itself, so they are not affected.
- A persistent core outbox (event rows written in the business transaction) would close the gap for
  every event. It is not part of this epic; reconsider if more mandatory events appear.

## 3. Contracts with other modules (added in N1/N2)

All of them return DTOs, are system-level (no `CurrentUserDTO`), and are used only by
notifications.

`property`:
- Migration: `core_users.locale` (nullable `et|ru|en`; `NULL` means the instance default
  `DEFAULT_LOCALE`).
- `PropertyService.resolve_recipients(building_id, scope, entrance, apartment_ids)
  -> list[RecipientDTO(user_id, locale)]`: target matching from one shared pure function (10.3).
- `PropertyService.users_with_roles(building_id, roles) -> list[RecipientDTO]`.
- `PropertyService.reading_submitters(apartment_ids) -> list[RecipientDTO]`: active members with
  `can_submit_readings`.
- `PropertyService.get_delivery_contact(user_id) -> DeliveryContactDTO(email)`. **Documented
  exception** to "no DTO carries a raw e-mail": it exists only for delivery, the caller keeps the
  value in memory for one send, and never logs or stores it. The `property/schemas.py` docstring
  and docs/ARCHITECTURE.md are updated to say so.

`meters`:
- `MetersService.open_periods_due_within(until) -> list[DuePeriodRefDTO(period_id, building_id,
  period_code, due_date)]` (not `PeriodDTO`: that name is a public API schema already).
- `MetersService.apartments_missing_readings(period_id) -> list[uuid]`.

`communication`:
- `CommunicationService.active_emergencies() -> list[EmergencyRefDTO]` across all buildings, for
  reconciliation.
- `CommunicationService.get_emergency_ref(post_id) -> EmergencyRefDTO | None` regardless of status,
  for rendering and the send-time check.
- `EmergencyRefDTO(post_id, building_id, author_user_id, is_active, target_scope, target_entrance,
  target_apartment_ids, title, category)`.

## 4. Data model (draft; final version in docs/SCHEMA.md, task N1)

`notification_channel_bindings`: where a user can be reached on an external channel.
- `id`, `user_id`, `channel` (`telegram|webpush`), `config` (JSONB: Telegram `chat_id`; WebPush
  `endpoint`, `p256dh`, `auth`), `address_hash` (SHA-256 of the chat id or endpoint),
  `created_at`, `revoked_at`. On revoke, `config` is wiped.
- Partial unique index on `(channel, address_hash) WHERE revoked_at IS NULL`: an active chat or
  endpoint belongs to one user (re-subscribing a shared browser moves it); a user may have several
  WebPush devices.
- E-mail has no binding row: a user is reachable by e-mail when `core_users.email` is set.
  `in_app` is always available.

`notification_preferences`: `user_id`, `event_type`, `channel`, `enabled`. No row means the
default from section 5.

`notification_link_codes`: `code_hash`, `user_id`, `expires_at`, `used_at` (Telegram linking).

`notification_deliveries`: one message to one user on one channel (and one device).
- `id`, `user_id`, `channel`, `binding_id` (nullable; set for telegram/webpush), `event_type`,
  `dedup_key`, `subject_type` (`post|period`), `subject_id`, `params` (JSONB, PII-free: e.g.
  `entrance`, `period_code`, `category`), `priority` (0 = mandatory first), `status`
  (`PENDING|SENDING|SENT|FAILED|SKIPPED`), `attempts`, `next_attempt_at`, `lease_until`,
  `last_attempt_at`, `error_code`, `trace_id`, `created_at`, `sent_at`,
  `read_at` (in-app only).
- Named constraint `UNIQUE NULLS NOT DISTINCT (dedup_key, user_id, channel, binding_id)`
  (Postgres 15+), so `ON CONFLICT ON CONSTRAINT` works.
- The in-app feed is the `in_app` rows of the user; title and link are rendered from
  `subject_type`, `subject_id` and `params`, so no message text is stored.

## 5. Rules (`policies.py`, pure functions)

Default preferences (a user can change the non-mandatory ones):

| Event type | in_app | email | telegram | webpush |
|---|---|---|---|---|
| emergency (mandatory) | on | on | on | on |
| emergency resolved | on | off | on | on |
| reading deadline | on | on | on | on |
| period in review (board) | on | on | off | off |

- Mandatory events ignore preferences, but use only channels where the user is reachable.
- Retry: up to 5 attempts, backoff 1 min, 5 min, 30 min, 2 h; non-retryable errors fail at once.
- Templates per event type and locale (`et`, `ru`, `en`).
- **What leaves the system on external channels:** event type, the emergency title (written by the
  board; only the board can publish emergencies), the target (e.g. "entrance 2"), period code and a
  deep link. Post bodies, comments and author names stay in the app.

## 6. Channels as plugins

New protocols in `src/plugins/registry.py`, next to `ReadingExporter`:

```python
class NotificationChannel(Protocol):
    name: str  # "email", "telegram", "webpush"

    async def send(self, message: OutgoingMessage) -> DeliveryResult: ...


class InboundChannel(Protocol):  # optional, implemented by telegram
    async def parse_inbound(self, headers: Mapping[str, str], body: bytes) -> InboundLink | None: ...
```

- `OutgoingMessage`: address (from the binding or the delivery contact), locale, subject, body,
  deep link. Built at send time, never stored or logged.
- `DeliveryResult`: `ok`, `retryable`, `error_code`, `revoke_binding` (no provider text in logs).
- Plugins have no database access. The notifications service owns bindings, codes and deliveries;
  plugins only format, verify and send.
- Enabled through `ENABLED_PLUGINS`, e.g. `common.csv_export,common.email_smtp,common.telegram`.

### 6.1 E-mail (`common.email_smtp`)
SMTP settings from env. Local development uses Mailpit in docker compose.

### 6.2 Telegram (`common.telegram`): linking
- Updates arrive by **webhook** `POST /api/v1/notifications/telegram/webhook`. The plugin checks the
  `X-Telegram-Bot-Api-Secret-Token` header (secret from env) and returns an `InboundLink`.
  No long polling (it would need exactly one runner).
- The user requests a link code in the UI: 8 characters, valid 10 minutes, single use, stored only
  as a SHA-256 hash, at most 5 codes per user per hour.
- The user sends `/start <code>` to the bot. Only `chat.type == "private"` is accepted, so alerts
  never go to a group chat.
- Unlinking in the UI revokes the binding. A Telegram 403 ("bot was blocked") revokes it too.

### 6.3 WebPush (`common.webpush`)
- VAPID keys from env. The browser subscription is stored as a binding (one per device).
- **Server-side request forgery guard:** the endpoint must be `https` and its host must be on the
  allowlist of push services (`fcm.googleapis.com`, `updates.push.services.mozilla.com`,
  `*.notify.windows.com`, `web.push.apple.com`); anything else is rejected with 422.
- A 404 or 410 from the push service revokes the binding.

## 7. Background loops

Both start in the app lifespan, stop gracefully on shutdown, and can be switched off with
`NOTIFICATIONS_DISPATCHER_ENABLED` / `NOTIFICATIONS_SCHEDULER_ENABLED` (tests switch them off and
call one tick directly).

- **Dispatcher** (every 5 s): claims due rows with a lease (protocol in 10.2).
- **Scheduler** (every 10 min): each tick runs in a short transaction under
  `pg_try_advisory_xact_lock`, so only one process works per tick. It
  1. reconciles active emergencies (section 2.2);
  2. publishes `ReadingDeadlineApproaching` for open periods whose `due_date` is within
     `READING_REMINDER_DAYS_BEFORE` (default 2). The `dedup_key` per period makes it once per
     period, also across restarts. `due_date` is stored in UTC; texts show it in the instance
     time zone `INSTANCE_TIMEZONE` (default `Europe/Tallinn`).

### Wiring
The subscriber, the dispatcher and the scheduler get a session factory and the plugin registry
injected in `create_app` (not the request-scoped dependencies), on a per-app event bus (10.1).
Tests override both, so handlers write inside the test transaction and use fake channels.

## 8. Privacy and observability

- No names, e-mails, phones, chat ids, endpoints or message bodies in logs or spans. Spans carry
  `event_type`, `channel`, `status`, `delivery_id`.
- Metrics: deliveries by channel and status, retry count, revoked bindings.
- A test asserts that nothing personal reaches logs or spans (like the existing PII tests).

## 9. Tasks

Each task is one issue, one branch from `develop`, one PR into `develop`, and passes the CTO
review before merge (see AGENTS.md).

| # | Task | Done when |
|---|---|---|
| N1 | Contract: SCHEMA.md, API.md, openapi.json, ARCHITECTURE.md, including section 3 (new methods of property, meters, communication) | docs merged, API reviewed by the owner |
| N2 | Module, migrations (incl. `core_users.locale`), subscriber, recipients, `in_app` channel, preferences and feed API | an emergency alert creates in-app notifications for exactly the targeted users; resolved goes to the same users; tests green |
| N3 | Dispatcher, retries, `common.email_smtp`, Mailpit in compose | alert e-mail visible in Mailpit with the same `trace_id`; a failing SMTP retries on schedule |
| N4 | `common.telegram` with webhook linking | alert arrives in a linked private chat; group chats and reused codes are rejected |
| N5 | `common.webpush` (backend) | push sent to every device of a user; non-allowlisted endpoints rejected; 410 revokes |
| N6 | Scheduler: reading reminders and emergency reconciliation | one reminder per period and user, also after a restart; a dropped emergency event is delivered on the next tick |

## 10. Decisions settled before N1

Taken after the second design review (2026-10-31). N1 turns them into the contract.

### 10.1 Event bus per app
The process-global `event_bus` would collect a new handler on every `create_app()` call (once per
test), and the lifespan does not run under the test client. So:
- the bus lives on `app.state`; `get_event_bus` reads it from there; the global instance is removed
  in N2;
- notifications subscribes in `create_app`, on that app's bus;
- one database-using handler per event in this module: the test override shares one session and
  the bus runs handlers concurrently.

### 10.2 Dispatcher: lease, at-least-once, priority
1. Claim in a short transaction: `SELECT ... WHERE (status = 'PENDING' AND next_attempt_at <= now())
   OR (status = 'SENDING' AND lease_until < now()) ORDER BY priority, next_attempt_at
   FOR UPDATE SKIP LOCKED LIMIT n`; set `status = 'SENDING'`, `lease_until = now() + 2 min`,
   `attempts = attempts + 1`; commit.
2. Run the send-time checks (10.5), then send with no transaction open.
3. Record the result in a second transaction: `SENT`, `FAILED`, or back to `PENDING` with the next
   `next_attempt_at`.

A crash between 2 and 3 leaves the row `SENDING`; it is sent again when the lease expires, so
delivery is **at least once** (a duplicate message is possible, a lost one is not). Partial index
`(priority, next_attempt_at) WHERE status IN ('PENDING', 'SENDING')`. Mandatory rows have
priority 0, so a burst of reminders never delays an emergency.

### 10.3 Recipients
- One pure function decides whether an apartment matches a target (`ALL_BUILDING`, `ENTRANCE`,
  `APARTMENT_LIST`); communication and property both use it, so post visibility and notification
  targeting cannot drift apart.
- Only active users (`core_users.is_active`).
- `ALL_BUILDING`: all active users with a membership in the building.
- **Emergency (owner decision):** the users of the target **plus** the chairman and board members,
  as a set of `user_id`s, so a person who is both an owner in the target and a board member gets
  one notification per channel. The author of the alert gets none.
- Reading reminders: `PropertyService.reading_submitters(apartment_ids)`.

### 10.4 Contracts
See section 3 (already updated): `EmergencyRefDTO` with `author_user_id`, `is_active` and
`building_id`; `get_emergency_ref(post_id)` regardless of status; `active_emergencies()` across
buildings; `DuePeriodRefDTO` instead of `PeriodDTO`.

### 10.5 Send-time checks and reconciliation
Before sending, the dispatcher re-checks and marks the row `SKIPPED` with an `error_code` when:
- the delivery is for `emergency:{post_id}` and the post is no longer active;
- the binding is revoked, the user is inactive, or the e-mail is gone;
- the preference was turned off (non-mandatory events only).

Reconciliation (owner decision: no late alerts on external channels): a tick creates deliveries
only for users who have **no row at all** for `emergency:{post_id}`, and only on bindings created
before the post. Users who join, link a channel or add a device during an emergency see it as the
banner in the app.

"Resolved" goes to users whose `emergency:{post_id}` delivery reached `SENT` on that channel. If
none did (for example the alert was resolved before any delivery), nobody gets "resolved".

### 10.6 Time
- `meters_periods.due_date` means the **end of the local deadline day** in `INSTANCE_TIMEZONE`
  (default `Europe/Tallinn`), stored as UTC. N1 fixes the SCHEMA.md example; N2 fixes the seed
  (today it stores 23:59:59 UTC, which is already the next day in Tallinn).
- Reminders go out from 09:00 local time on the day `READING_REMINDER_DAYS_BEFORE` (default 2)
  before the deadline, never at night.

### 10.7 Telegram and WebPush hardening
- Link codes in `notification_link_codes` (hash only, 10 minutes, single use, 5 per user per hour);
  at most 10 `/start` attempts per `chat_id` per hour.
- Webhook: the secret header is compared in constant time; 404 when the plugin is disabled; 200 for
  every update with a valid secret (otherwise Telegram retries); body size limit 64 KB;
  `setWebhook` is an admin command, not run on startup; dev and prod use separate bots.
- WebPush endpoint check: parse with `urllib`; `https` only; no userinfo, no IP literals, default
  port; host equals an allowlisted host or ends with `"." + host` for wildcard entries; no
  redirects; 10 s timeout. The device binding is revoked on logout.

### 10.8 In-app and retention
- `in_app` rows are inserted as `SENT` and never go through the dispatcher; the feed shows them.
- Delivery rows are deleted after 180 days (scheduler job).

### 10.9 Not done here
- `telegram_notifier` in docs/ARCHITECTURE.md becomes `common.telegram` in N1.
