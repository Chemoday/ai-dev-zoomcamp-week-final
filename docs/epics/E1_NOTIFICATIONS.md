# E1. Notifications

- **Phase:** 2. **Status:** N1, N1b and N2-N6 done (in-app notifications, the dispatcher, e-mail, Telegram, WebPush, reading reminders and clean-up in `develop`; external channels send nowhere until a real login, 10.10); N7 (mobile push) waits for B-7. **Epic issue:** [#39](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/39).
- **Goal:** one pluggable notification system that every module uses, instead of each feature
  sending its own messages. Reminders, emergency alerts and (later) voting all go through it.

This page is the design. The binding contract is docs/SCHEMA.md section 5, docs/API.md section 6
and docs/ARCHITECTURE.md section 3 (task N1); where they differ, the contract wins.

## 1. Scope

In scope:
- A new module `src/modules/notifications/` (the standard vertical slice: `models.py`,
  `schemas.py`, `policies.py`, `service.py`, `router.py`, `events.py`).
- A built-in `in_app` channel (a notification feed in the UI).
- Delivery channels as plugins: `common.email_smtp`, `common.telegram`, `common.webpush`, and
  `common.fcm_push` for the native phone app (planned, N7, 6.4).
- Per-user preferences (event type x channel).
- Background loops: a dispatcher (sends and retries) and a scheduler (reminders, clean-up).

Out of scope (later epics or backlog):
- Debt reminders: need payment data, see docs/BACKLOG.md B-6.
- SMS (a paid provider; can be one more channel plugin later).
- Quiet hours (not in this epic).
- Live updates of the in-app feed (WebSocket, server-sent events): the client polls (6.5).

## 2. How it works

```text
business transaction commits
  -> the domain event is stored in core_domain_events and published after commit (or by the relay)
  -> notifications subscriber: resolve recipients, apply preferences,
     INSERT notif_deliveries (PENDING) -- never sends
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
| reading deadline near (the scheduler, no domain event; 7) | notifications | members with `can_submit_readings` of apartments missing a reading | no | `reading_deadline:{period_id}` |
| `ReadingPeriodStatusChanged` -> `REVIEW_BY_BOARD` | meters | chairman and accountants | no | `period_review:{period_id}:{event_id}` |

- The user who caused an event (`published_by_user_id`, `changed_by_user_id`, ...) is not notified
  about their own action.
- "Missing a reading": at least one active apartment meter of the apartment has no reading in the
  period.

### 2.2 Reliability: what happens if the process dies

Domain events are stored in `core_domain_events` in the same transaction as the change, and a relay
publishes any event a crash left unpublished (owner decision 2026-10-31, task N1b;
docs/BACKEND_CONVENTIONS.md 2.B). An event reaches the subscriber at least once, and is retried
until the subscriber's insert succeeds (a row is marked published only when every handler
succeeded). The subscriber is idempotent through `dedup_key`.

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
- `PropertyService.is_user_active(user_id) -> bool` (send-time check).
- `PropertyService.get_delivery_contact(user_id) -> DeliveryContactDTO(email)`. **Documented
  exception** to "no DTO carries a raw e-mail": it exists only for delivery, the caller keeps the
  value in memory for one send, and never logs or stores it. The `property/schemas.py` docstring
  and docs/ARCHITECTURE.md are updated to say so.

`meters`:
- `MetersService.open_periods_due_within(until) -> list[DuePeriodRefDTO(period_id, building_id,
  period_code, due_date)]` (not `PeriodDTO`: that name is a public API schema already).
- `MetersService.apartments_missing_readings(period_id) -> list[uuid]`.

`communication`:
- `CommunicationService.get_emergency_ref(post_id) -> EmergencyRefDTO | None` regardless of status,
  for rendering and the send-time check.
- `EmergencyRefDTO(post_id, building_id, author_user_id, is_active, target_scope, target_entrance,
  target_apartment_ids, title, category)`.

## 4. Data model (draft; final version in docs/SCHEMA.md, task N1)

`notif_channel_bindings`: where a user can be reached on an external channel.
- `id`, `user_id`, `channel` (`telegram|webpush`), `config` (JSONB: Telegram `chat_id`; WebPush
  `endpoint`, `p256dh`, `auth`), `address_hash` (keyed HMAC-SHA-256 of the chat id or endpoint),
  `created_at`, `revoked_at`. On revoke, `config` is wiped.
- Partial unique index on `(channel, address_hash) WHERE revoked_at IS NULL`: an active chat or
  endpoint belongs to one user (re-subscribing a shared browser moves it); a user may have several
  WebPush devices.
- E-mail has no binding row: a user is reachable by e-mail when `core_users.email` is set.
  `in_app` is always available.

`notif_preferences`: `user_id`, `event_type`, `channel`, `enabled`. No row means the
default from section 5.

`notif_link_codes`: `code_hash`, `user_id`, `expires_at`, `used_at` (Telegram linking).
`notif_link_attempts`: `address_hash`, `attempted_at`, `succeeded` (rate limit of `/start` per chat).

`notif_deliveries`: one message to one user on one channel (and one device).
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
- **Message texts (owner decision 2026-10-31, task N3).** Two kinds:
  - **custom**, written by people: an emergency message is the post's title and body exactly as
    the board wrote them (any language, or several);
  - **system**, fixed in the code: the subject lines, "emergency resolved", the reading reminder,
    "period in review", the target line and the footer. They are rendered in the recipient's
    locale from one file, `src/modules/notifications/messages.csv` (`key,en,ru,et`; a new language
    is a new column plus a `Locale` value). The chairman cannot change system texts.
  Every e-mail ends with a fixed footer that links to the site, where the contact channels of the
  association are listed.
- **What leaves the system on external channels:** the event type, the emergency title **and
  body** (written by the board; only the board can publish emergencies), the target (e.g.
  "entrance 2"), the period code and due date, and a link to the site. Comments, forum posts and
  author names stay in the app.

## 6. Channels as plugins

New protocols in `src/plugins/registry.py`, next to `ReadingExporter`:

```python
class NotificationChannel(Protocol):
    name: str  # "email", "telegram", "webpush", "mobile_push"

    async def send(self, message: OutgoingMessage) -> DeliveryResult: ...


class InboundChannel(Protocol):  # optional, implemented by telegram
    def verify(self, headers: Mapping[str, str]) -> bool: ...

    def parse_inbound(self, body: bytes) -> InboundLink | None: ...
```

- `OutgoingMessage`: address (from the binding or the delivery contact), locale, event type,
  subject, body, deep link, plus `urgent`, `collapse_key` and `expires_at` for push channels. Built
  at send time, never stored or logged.
- `DeliveryResult`: `ok`, `retryable`, `error_code`, `revoke_binding` (no provider text in logs).
- Plugins have no database access. The notifications service owns bindings, codes and deliveries;
  plugins only format, verify and send.
- Enabled through `ENABLED_PLUGINS`, e.g. `common.csv_export,common.email_smtp,common.telegram`.

### 6.1 E-mail (`common.email_smtp`, task N3)
**The app is an SMTP client only** (owner decision 2026-10-31). Each backend connects with
`aiosmtplib` to an external transactional e-mail provider; no image contains a mail server, and
there is no shared relay container on the VPS:
- several environments on one VPS (dev, prod) simply have their own `SMTP_*` values in their
  `.env` (their own account, or at least their own sender address);
- a local relay would answer "accepted" at once, so the dispatcher could not see failures, and its
  own queue would retry behind our back and break the one-hour rule for emergencies;
- sending directly from the VPS (own MTA) would land in spam (shared hosting IP ranges, no PTR,
  SPF, DKIM and DMARC warm-up).

Real sending needs an own domain for SPF/DKIM (none is registered yet) and a real login
(docs/BACKLOG.md B-7); until then every environment runs in demo mode and sends nothing (10.10).
The provider is chosen then; any provider with SMTP submission works, so a self-hosted association
uses its own (docs/BACKLOG.md B-8).

- Connection per message, `SMTP_TLS`: `starttls` (port 587, default), `tls` (implicit TLS, 465) or
  `none` (only for a local test server); 10 s timeout.
- The message is plain text, UTF-8: `From: SMTP_FROM`, `To`, `Subject`, `Date`, `Message-ID` and
  `X-Trace-Id` (the trace of the request that caused the event, so support can follow one e-mail
  back to its request; it is not personal data).
- Results: a `4xx` reply, a timeout or a connection error is retryable (`SMTP_TEMPORARY`,
  `SMTP_UNAVAILABLE`); a `5xx` reply fails at once (`SMTP_REJECTED`), an authentication failure
  too (`SMTP_AUTH_FAILED`, a configuration error), and so does a server that lacks what the
  settings need, e.g. STARTTLS (`SMTP_NOT_SUPPORTED`). Bounces that arrive later need the
  provider's webhooks and are not handled here.
- The plugin refuses to start without `SMTP_HOST` or a valid `SMTP_FROM`, and with credentials
  over `SMTP_TLS=none` (they would travel in clear text).
- Tests run an in-process SMTP server (`aiosmtpd`), no container. For looking at e-mails by hand,
  `docker compose --profile mail up` starts Mailpit (web UI on port 8025); it is never part of the
  image or the VPS deployment.

### 6.2 Telegram (`common.telegram`): linking
- Updates arrive by **webhook** `POST /api/v1/notifications/telegram/webhook`. The plugin checks the
  `X-Telegram-Bot-Api-Secret-Token` header (secret from env) and returns an `InboundLink`.
  No long polling (it would need exactly one runner).
- The user requests a link code in the UI: 8 characters, valid 10 minutes, single use, stored only
  as a keyed HMAC, at most 5 codes per user per hour.
- The user sends `/start <code>` to the bot. Only `chat.type == "private"` is accepted, so alerts
  never go to a group chat.
- Unlinking in the UI revokes the binding. A Telegram 403 ("bot was blocked") revokes it too.

### 6.3 WebPush (`common.webpush`)
- VAPID keys from env. The browser subscription is stored as a binding (one per device).
- **Server-side request forgery guard:** the endpoint must be `https` and its host must be on the
  allowlist of push services (`fcm.googleapis.com`, `updates.push.services.mozilla.com`,
  `*.notify.windows.com`, `web.push.apple.com`); anything else is rejected with 422.
- A 404 or 410 from the push service revokes the binding.
- Encryption RFC 8291 (`aes128gcm`, one record) and VAPID RFC 8292 (ES256 JWT), implemented on
  `cryptography` and `pyjwt` and checked against the example of RFC 8291 Appendix A; no
  `pywebpush` (synchronous, pulls `requests`, no control over redirects or the test transport).
  The payload is JSON for the service worker without the e-mail footer (docs/API.md 6); `Topic` is
  a hash of the collapse key, so the push service sees no post id.

### 6.4 Mobile push (`common.fcm_push`, task N7, planned)
Owner decision 2026-10-31: the phone app is a native app or a wrapper of the Vue client (e.g.
Capacitor with a Firebase Messaging plugin such as `@capacitor-firebase/messaging`; the stock
`@capacitor/push-notifications` returns the raw APNs token on iOS), and its pushes go through
**Firebase Cloud Messaging (FCM HTTP v1)**. FCM delivers to Android directly and to iOS through
APNs, so the backend talks to one service. A PWA keeps using
WebPush (6.3). Only a stub exists so far (`src/plugins/common/fcm_push.py`, it refuses to load).

**Device registration.** The app gets an FCM registration token from the Firebase Messaging SDK (on
iOS the FCM token, not the raw APNs token) and sends it to
`POST /api/v1/notifications/mobile-push/devices` (docs/API.md 6). It becomes a binding of channel
`mobile_push`: `config = {"token", "platform"}`, `address_hash = HMAC(token)`, the same move-to-the-
new-user rule and lock order as WebPush, at most 10 active devices per user. On a token refresh the
app registers the new token with `replaces_binding_id` (its previous binding, revoked in the same
transaction before the device limit is checked, so a user at the limit keeps receiving pushes); on
logout it deletes the binding and calls `deleteToken`.

**Sending.** `POST https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send` with an
OAuth 2 access token of a service account (scope `https://www.googleapis.com/auth/firebase.messaging`,
via `google-auth`, cached until it expires). The service account key is a mounted secret file
(`FCM_SERVICE_ACCOUNT_FILE`), never an environment value or a log line. One request per device, 10 s
timeout, no redirects. The message:
- `notification`: title and body rendered in the delivery's locale (the same texts as Telegram);
- `data`: `event_type` and `link` (the deep link, which already names the post or period), so the
  app opens the right screen; both come from `OutgoingMessage`;
- **emergencies** (`EMERGENCY_PUBLISHED`, `urgent`): Android `priority: HIGH` on the notification
  channel `emergency` (created by the app), APNs `apns-priority: 10` and `interruption-level:
  time-sensitive`;
- **everything else**: Android normal priority on the channel `general`, APNs priority 5;
- **expiry** from `OutgoingMessage.expires_at`, set on both platforms: `android.ttl` (seconds left)
  and the APNs header `apns-expiration` (UNIX time); the Android TTL alone does not reach iOS.
  Emergencies expire 1 hour after the delivery row was created, so a retry sends only the time
  left. The dispatcher skips an emergency delivery whose hour is up on every external channel
  (`SKIPPED`, `EMERGENCY_TOO_OLD`, docs/ARCHITECTURE.md 3.3), so the retry backoff cannot bring an
  alert late. The row itself is created at most 1 hour after the alert (late-event rule), so
  nothing goes out later than about 2 hours after it. Other events expire after 24 hours;
- **replacing:** the key `post:{post_id}` for both emergency events (a resolved alert replaces the
  published one) and `{event_type}:{period_id}` for period events (a deadline reminder and a review
  notice do not replace each other). It is sent as `android.notification.tag` (replaces a shown
  notification), `android.collapse_key` (collapses queued ones) and `apns-collapse-id`.

**FCM errors** (`DeliveryResult`, error codes stored without provider text). The mapping reads
`error.details[].errorCode` of the type `google.firebase.fcm.v1.FcmError`, not the gRPC
`error.status`:

| `errorCode` | HTTP | Result |
|---|---|---|
| `UNREGISTERED` | 404 | revoke the binding, `FCM_UNREGISTERED` |
| `SENDER_ID_MISMATCH` | 403 | revoke the binding, `FCM_SENDER_MISMATCH` |
| `QUOTA_EXCEEDED`, `UNAVAILABLE`, `INTERNAL` | 429, 503, 500 | retryable (the dispatcher's backoff; FCM's `Retry-After` is not used yet, a `retry_after` in `DeliveryResult` can come with N7) |
| `INVALID_ARGUMENT` | 400 | failed, no retry, `FCM_INVALID_ARGUMENT` (the payload is built by us, so this is a bug or an invalid token; not revoked) |
| `THIRD_PARTY_AUTH_ERROR` | 401 | failed, no retry, `FCM_AUTH_ERROR`: the APNs key in Firebase is wrong; counted in a metric for an alert |
| `UNAUTHENTICATED` (no `errorCode`) | 401 | the access token expired early or the clock is off: refresh the credentials and retry once, then retryable; a mandatory emergency must not be dropped for this |
| service account file unreadable | | failed at startup (startup check), never per message |
| timeout, connection error | | retryable |

**Privacy.** Unlike WebPush, an FCM notification is not end-to-end encrypted: Google (and Apple for
iOS) can read it. It therefore carries exactly what the other external channels may carry (section
5: event type, emergency title and body, target, period code, link), never comments or names.
Tokens never appear in logs, spans or API responses.

**Done when (N7):** a migration adds `mobile_push` to the channel checks (`notif_channel_bindings`,
`notif_deliveries`, the preference matrix grows to 20 combinations, defaults as `webpush`); the
device endpoint and the plugin exist; tests with a fake FCM server: a push reaches every device of a
user; emergencies are sent with high priority and with both `android.ttl` and `apns-expiration`
counting down from 1 hour; a retry after the hour is skipped; `UNREGISTERED` revokes; a retryable
error is retried; `UNAUTHENTICATED` refreshes the credentials; a token refresh at the device limit
works through `replaces_binding_id`; demo mode registers nothing; no token in logs.

**Needs first:** N3 (dispatcher), a real login (docs/BACKLOG.md B-7), and on the app side a Firebase
project, an APNs key uploaded to Firebase and a store build (frontend repository, not this one).

### 6.5 How clients get in-app notifications (owner decision 2026-10-31)
No WebSocket and no server-sent events. The client polls `GET /api/v1/notifications/unread-count`
every 60 s while the tab is visible and loads the feed when the count changes or the bell is opened
(docs/FRONTEND_GUIDE.md section 11). Reasons:
- the load is tiny (one indexed count; 200 residents polling once a minute is about 3 requests per
  second);
- a socket would need long-lived connections through Caddy, reconnect handling and, with more than
  one worker, a shared pub/sub (e.g. Redis) to fan out events;
- a socket only helps while the tab is open; a closed app is reached by e-mail, Telegram, WebPush
  or mobile push anyway, and the emergency banner already polls.
If live updates are ever needed, server-sent events on the same feed are the next step, not a
WebSocket.

## 7. Background loops

Both start in the app lifespan, stop gracefully on shutdown, and can be switched off with
`NOTIFICATIONS_DISPATCHER_ENABLED` / `NOTIFICATIONS_SCHEDULER_ENABLED` (tests switch them off and
call one tick directly).

- **Dispatcher** (every 5 s): claims due rows with a lease (protocol in 10.2).
- **Scheduler** (every 10 min): each tick runs in short transactions under
  `pg_try_advisory_xact_lock`, so only one process works at a time. It
  1. inserts READING_DEADLINE notifications for open periods: from `READING_REMINDER_DAYS_BEFORE`
     (default 2) days before the local deadline day, 09:00-21:00 local time
     (`INSTANCE_TIMEZONE`, default `Europe/Tallinn`; `due_date` is stored in UTC). The rows
     themselves mark the period as reminded, so it is once per period, also across restarts; no
     domain event is published (docs/ARCHITECTURE.md 3.3).
  2. deletes old deliveries, revoked bindings and link rows (docs/SCHEMA.md 5).

### Wiring
The subscriber, the dispatcher and the scheduler get a session factory and the plugin registry
injected in `create_app` (not the request-scoped dependencies), on a per-app event bus (10.1).
Tests override both, so handlers write inside the test transaction and use fake channels.

## 8. Privacy and observability

- No names, e-mails, phones, chat ids, endpoints, FCM tokens or message bodies in logs or spans.
  Spans carry `event_type`, `channel`, `status`, `delivery_id`.
- Metrics: deliveries by channel and status, retry count, revoked bindings.
- A test asserts that nothing personal reaches logs or spans (like the existing PII tests).

## 9. Tasks

Each task is one issue, one branch from `develop`, one PR into `develop`, and passes the CTO
review before merge (see AGENTS.md).

| # | Task | Done when |
|---|---|---|
| N1 | Contract: SCHEMA.md, API.md, ARCHITECTURE.md, including sections 3 and 10 (openapi.json is generated from code, so it follows in N2) | docs merged, API reviewed by the owner |
| N1b | Durable domain events in core (`core_domain_events`, relay) | an event survives a crash between commit and publish; rolled-back changes leave no event; tests green |
| N2 | Module, migrations (incl. `core_users.locale`), subscriber, recipients, `in_app` channel, preferences and feed API | an emergency alert creates in-app notifications for exactly the targeted users; resolved goes to the same users; tests green |
| N3 | Dispatcher, retries, `common.email_smtp` | tests (demo mode off, test JWT) with a local SMTP test server: the alert e-mail arrives with the same `trace_id`; a failing SMTP retries on schedule; demo mode sends nothing |
| N4 | `common.telegram` with webhook linking | tests with a fake Telegram API: alert reaches a linked private chat; group chats and reused codes are rejected; demo mode links nothing |
| N5 | `common.webpush` (backend) | tests with a fake push service: push to every device of a user; non-allowlisted endpoints rejected; 410 revokes; demo mode subscribes nothing |
| N6 | Scheduler: reading reminders and clean-up | one reminder per period and user, also after a restart; old deliveries and link codes are deleted |
| N7 | `common.fcm_push`: push to the native phone app (6.4). Plan and stub done (#57); implementation later | see "Done when" in 6.4 |

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
`building_id`; `get_emergency_ref(post_id)` regardless of status; `DuePeriodRefDTO` instead of
`PeriodDTO`.

### 10.5 Send-time checks
Before sending, the dispatcher re-checks and marks the row `SKIPPED` with an `error_code` when:
- the delivery is for `emergency:{post_id}` and the post is no longer active;
- the binding is revoked, the user is inactive, or the e-mail is gone;
- the preference was turned off (non-mandatory events only).

Users who join, link a channel or add a device during an emergency see it as the banner in the
app; recipients are resolved when the event is processed (normally seconds after it happened). An
`EMERGENCY_PUBLISHED` event processed more than 1 hour late (after an outage) creates `in_app`
notifications only, which keeps the owner rule "no late alerts on external channels".

"Resolved" goes to users whose `emergency:{post_id}` delivery reached `SENT` on that channel. If
none did (for example the alert was resolved before any delivery), nobody gets "resolved".

### 10.6 Time
- `meters_periods.due_date` means the **end of the local deadline day** in `INSTANCE_TIMEZONE`
  (default `Europe/Tallinn`), stored as UTC. N1 fixes the SCHEMA.md example; N2 fixes the seed
  (today it stores 23:59:59 UTC, which is already the next day in Tallinn).
- Reminders go out between 09:00 and 21:00 local time, starting on the day `READING_REMINDER_DAYS_BEFORE` (default 2)
  before the deadline, never at night.

### 10.7 Telegram and WebPush hardening
- Link codes in `notif_link_codes` (hash only, 10 minutes, single use, 5 per user per hour);
  at most 10 `/start` attempts per `chat_id` per hour.
- Webhook: the secret header is compared in constant time; 404 when the plugin is disabled; 200 for
  every update with a valid secret (otherwise Telegram retries); body size limit 64 KB;
  `setWebhook` is an admin command (`python -m src.plugins.common.telegram set-webhook`), not run
  on startup; dev and prod use separate bots.
- WebPush endpoint check: parse with `urllib`; `https` only; no userinfo, no IP literals, default
  port; host equals an allowlisted host or ends with `"." + host` for wildcard entries; no
  redirects; 10 s timeout. The device binding is revoked on logout.

### 10.8 In-app and retention
- `in_app` rows are inserted as `SENT` and never go through the dispatcher; the feed shows them.
- Delivery rows are deleted after 180 days (scheduler job).

### 10.9 Not done here
- Done in N1: `telegram_notifier` in docs/ARCHITECTURE.md is now `common.telegram`.

### 10.10 Refined by the contract review (N1)
The contract (docs/SCHEMA.md 5, docs/API.md 6, docs/ARCHITECTURE.md 3) is binding; it refines this
page as follows:
- The claim only takes rows with `attempts < 5`; an expired lease at 5 attempts becomes `FAILED`.
- Demo mode (`DEMO_MODE_ENABLED=true`) delivers `in_app` only; external linking is refused.
- The app refuses to start with an external channel enabled but its secrets or `PUBLIC_BASE_URL`
  missing; the webhook secret has at least 32 characters.
- One Telegram chat per user; re-linking moves or replaces the binding in one transaction.
- Addresses and codes are hashed with a keyed HMAC (`NOTIFICATIONS_HASH_SECRET`).
- The locale is stored on each delivery; `period_review` keys use the event id; reminders are sent
  between 09:00 and 21:00 local time.
- `InboundChannel` is split into `verify()` and `parse_inbound()`.
- **Owner decision on external channels:** nowhere for now. Every environment runs in demo mode,
  which delivers `in_app` only, with no override; e-mail, Telegram and WebPush are accepted by
  automated tests with demo mode off. Real use needs a real login (docs/BACKLOG.md B-7).
- The dispatcher's result update is fenced on `status` and `attempts`; the lease is 5 minutes;
  linking and subscribing lock the user before the address.
- **Durable domain events (owner decision, after N1):** events are stored in `core_domain_events`
  with the change and relayed after a crash (task N1b). This replaced the earlier emergency
  reconciliation (30-minute window, "created before" rules, startup tick), which is gone.
