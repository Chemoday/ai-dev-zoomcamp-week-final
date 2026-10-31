# E1. Notifications

- **Phase:** 2. **Status:** design. **Epic issue:** see the `epic` label on GitHub.
- **Goal:** one pluggable notification system that every module uses, instead of each feature
  sending its own messages. Reminders, emergency alerts and (later) voting all go through it.

## 1. Scope

In scope:
- A new module `src/modules/notifications/` (the standard vertical slice: `models.py`,
  `schemas.py`, `policies.py`, `service.py`, `router.py`, `events.py`).
- A built-in `in_app` channel (a notification feed in the UI).
- Delivery channels as plugins: `common.email_smtp`, `common.telegram`, `common.webpush`.
- Per-user preferences (event type x channel).
- A time-based trigger: reading deadline reminders.

Out of scope (later epics or backlog):
- Debt reminders: need payment data, see docs/BACKLOG.md B-6.
- SMS (a paid provider; can be one more channel plugin later).

## 2. How it works

```text
domain event (after commit, EventOutbox)
  -> notifications subscriber
  -> recipients via PropertyService (DTOs: user ids, locale; no contact data)
  -> policies: preferences, mandatory events, deduplication
  -> rows in notification_deliveries (PENDING)
  -> dispatcher -> channel plugin .send() -> SENT / FAILED (retry with backoff)
```

Events handled first:

| Event | Source | Recipients | Mandatory |
|---|---|---|---|
| `EmergencyAlertPublished` | communication | residents in the alert target | yes |
| `EmergencyAlertResolved` | communication | same target | no |
| `ReadingDeadlineApproaching` (new) | notifications scheduler | members with `can_submit_readings` and no reading in the open period | no |
| `ReadingPeriodStatusChanged` -> `REVIEW_BY_BOARD` | meters | chairman, accountant | no |

The module only subscribes to events. Other modules never import it, so it can be switched off
and the rest of the system works unchanged.

## 3. Channels as plugins

A new protocol in `src/plugins/registry.py`, next to `ReadingExporter`:

```python
class NotificationChannel(Protocol):
    name: str  # "email", "telegram", "webpush"

    async def send(self, message: OutgoingMessage) -> DeliveryResult: ...
```

- `OutgoingMessage` is a DTO: channel address, locale, subject, body, deep link. It is built by
  the notifications service at send time and never stored or logged.
- `DeliveryResult`: `ok`, `retryable`, `error_code` (no free text from the provider in logs).
- Channels are enabled through `ENABLED_PLUGINS`, e.g.
  `common.csv_export,common.email_smtp,common.telegram`.

Channel notes:
- **email_smtp:** SMTP settings from env. Local development uses Mailpit in docker compose.
- **telegram:** one bot per instance (token from env). The user links a chat by sending a one-time
  code from the UI to the bot; the bot stores the `chat_id` binding.
- **webpush:** VAPID keys from env, the browser subscription is stored as a binding. The UI part is
  done in the frontend repository.

## 4. Data model (draft, final version goes to docs/SCHEMA.md in task N1)

- `notification_preferences` (`user_id`, `event_type`, `channel`, `enabled`). No row means the
  default for that event type.
- `notification_channel_bindings` (`user_id`, `channel`, `address`, `verified_at`): the Telegram
  `chat_id` or the WebPush subscription.
- `notification_deliveries` (`id`, `user_id`, `event_type`, `event_id`, `channel`, `status`
  `PENDING|SENT|FAILED|SKIPPED`, `attempts`, `error_code`, `trace_id`, `created_at`, `sent_at`),
  with `UNIQUE(event_id, user_id, channel)` for deduplication.
- In-app feed: `notification_deliveries` rows of channel `in_app` plus `read_at`.

E-mail addresses stay in `property`. The notifications service asks `PropertyService` for the
address of one user right before sending and keeps it only in memory.

## 5. Rules (`policies.py`, pure functions)

- Mandatory events (`EmergencyAlertPublished`) ignore preferences and quiet hours, but only on
  channels the user has bound.
- One delivery per event, user and channel.
- Retry: up to 5 attempts with exponential backoff; non-retryable errors fail at once.
- Templates per event type and locale (`et`, `ru`, `en`). Authors appear only as masked badges.

## 6. Scheduler

There is no scheduler yet. Proposal: an asyncio background loop started in the app lifespan,
guarded by a Postgres advisory lock so only one process runs it. Every hour it checks open
periods and publishes `ReadingDeadlineApproaching` N days (setting, default 2) before the 4th-day
deadline. It also picks up `PENDING` deliveries whose retry time has come.

## 7. Privacy and observability

- No names, e-mails, phones, chat ids or message bodies in logs or spans. Spans carry
  `event_type`, `channel`, `status`, `delivery_id`.
- Metrics: deliveries by channel and status, retry count.
- A test asserts that nothing personal reaches logs or spans (like the existing PII tests).

## 8. Tasks

Each task is one issue, one branch from `develop`, one PR into `develop`, and passes the CTO
review before merge (see AGENTS.md).

| # | Task | Done when |
|---|---|---|
| N1 | Contract: SCHEMA.md, API.md, openapi.json, ARCHITECTURE.md | docs merged, API reviewed by the user |
| N2 | Module, migration, `in_app` channel, preferences and feed API | an emergency alert creates in-app notifications; tests green |
| N3 | Dispatcher, retries, `common.email_smtp`, Mailpit in compose | alert e-mail visible in Mailpit with the same `trace_id` |
| N4 | `common.telegram` plugin with chat linking | alert arrives in a linked Telegram chat |
| N5 | `common.webpush` plugin (backend) | push sent to a stored subscription |
| N6 | Scheduler and reading deadline reminders | reminder sent only to members without a reading |
