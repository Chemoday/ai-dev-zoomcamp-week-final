# REST API Specification (FastAPI `/api/v1`)

All responses include `X-Trace-Id` header populated by OpenTelemetry middleware for observability correlation.

**Machine-readable contract:** [`docs/openapi.json`](openapi.json) (OpenAPI 3.1), generated from the app. Build the frontend against it; this file explains the rules behind it.
- Every endpoint lists its error statuses, and each status lists its possible `error_code` values. All errors use the `ErrorResponse` schema (section 0).
- Meter values are strings with a pattern in both directions (`"1254.100"`); numbers are rejected.
- TypeScript types: `npx openapi-typescript docs/openapi.json -o src/api/schema.ts`.
- Interactive docs of a running instance: `/docs` (Swagger UI) and `/openapi.json`.
- After any API change, regenerate the file with `uv run python -m src.core.openapi`; `tests/core/test_openapi.py` fails while it is out of date.

## 0. Authentication & Error Format

Error `message` texts are **English only**. They are meant for developers and logs; the frontend shows localized texts chosen by `error_code`. The backend contains no Russian at all: author badges are English too (`Apt. 14`, `Chairman`).

Every `/api/v1` endpoint requires an authenticated user:
- `Authorization: Bearer <jwt>`: HS256 token with `sub = <user uuid>` and `exp`. It is accepted only when `JWT_SECRET_KEY` is configured.
- `X-Demo-User-Id: <user uuid>`: accepted only when `DEMO_MODE_ENABLED=true` (local Docker, tests and the public demo). If both headers are sent, the Bearer token wins.

All errors share one JSON shape, `{"error_code": "...", "message": "...", ...extra}`:

| Status | `error_code` | When |
| :--- | :--- | :--- |
| 401 | `NOT_AUTHENTICATED` / `INVALID_TOKEN` / `UNKNOWN_USER` | No credentials, a bad or expired token, or an unknown or inactive user (sent with `WWW-Authenticate: Bearer`) |
| 403 | `FORBIDDEN` | Authenticated, but the building role or a membership flag (e.g. `can_submit_readings`) does not allow it |
| 404 | `NOT_FOUND`, `MEMBERSHIP_NOT_FOUND`, `METER_NOT_FOUND`, `PERIOD_NOT_FOUND`, `READING_NOT_FOUND`, `POST_NOT_FOUND`, `EXPORTER_NOT_ENABLED` | Unknown route, a resource that does not exist or is not visible to you, or a disabled plugin |
| 409 | `ANOMALY_CONFIRMATION_STEP_1/2`, `PERIOD_CLOSED`, `PERIOD_LOCKED`, `PERIOD_NOT_IN_REVIEW`, `INVALID_PERIOD_TRANSITION`, `READING_SET_BY_BOARD`, `LATER_READING_EXISTS`, `COMMENTS_DISABLED`, `POST_NOT_DELETABLE` | The request is valid, but conflicts with the current state |
| 405 | `METHOD_NOT_ALLOWED` | The route exists but not for this HTTP method |
| 422 | `VALIDATION_ERROR`, `READING_BELOW_PREVIOUS`, `REASON_REQUIRED`, `ESTIMATE_OUT_OF_RANGE`, `INVALID_TARGET`, `INVALID_AUTHOR_APARTMENT` | Invalid input; for `VALIDATION_ERROR` the details are in `errors`. Free-text fields reject control characters, e.g. NUL |
| 500 | `INTERNAL_SERVER_ERROR` | Unhandled error; the body carries `trace_id` |
| other | `HTTP_<status>` | Any other framework-level HTTP error |

### Author badge rules (`property.policies.format_author_badge`)
| `privacy_display_mode` | With apartment | Without apartment context |
| :--- | :--- | :--- |
| `APARTMENT_ONLY` (default) | `Apt. 14` | Role label: `Chairman` / `Board` / `Accountant` / `Resident` |
| `INITIALS` | `I. P. (Apt. 14)` | `I. P.` |
| `FULL_NAME` | `Ivan Petrov (Apt. 14)` | `Ivan Petrov` |

- **With an apartment**, the mode is that membership's mode. If the author is no longer a member of that apartment, `APARTMENT_ONLY` is used, because nobody consented to show the name there.
- **Without an apartment context** (e.g. a board notice), the mode is the *most private* of the author's memberships, so consenting to be named in one apartment never reveals the name anywhere else. A user with no membership (e.g. an external accountant) always shows the role label.

`role_badge`: `BOARD` for `CHAIRMAN` / `BOARD` building roles (this takes precedence, so neighbours know they are talking to the board), `ACCOUNTANT` for accountants, otherwise `OWNER` for owners and `RESIDENT` for tenants and residents.

---

## 1. Profile & Privacy Settings (`property`)

### `GET /api/v1/property/me`
Returns the current user's role, apartments, permissions, and active privacy display mode.
The payload never contains the raw `full_name`, `email` or `phone`, not even the user's own. Names appear only inside a `preview_badge`, and only to the degree the chosen display mode reveals them.

**Response 200 OK:**
```json
{
  "user_id": "uuid",
  "building_id": "uuid",
  "building_role": "USER",
  "privacy_display_mode": "APARTMENT_ONLY",
  "memberships": [
    {
      "membership_id": "uuid",
      "apartment_id": "uuid",
      "apartment_number": "14",
      "entrance": 2,
      "floor": 4,
      "relation_type": "OWNER",
      "can_submit_readings": true,
      "can_view_bills": true,
      "can_vote": true,
      "privacy_display_mode": "APARTMENT_ONLY",
      "preview_badge": { "display_label": "Apt. 14", "role_badge": "OWNER" }
    }
  ]
}
```
Memberships are ordered by entrance, then apartment number in natural order (`2`, `10`, `14`, `14A`). The top-level `privacy_display_mode` is the most private mode across the memberships (`APARTMENT_ONLY` when there are none); it is the mode used when the user acts without an apartment context. Only memberships of apartments in the user's own building are returned.

### `PATCH /api/v1/property/memberships/{membership_id}/privacy`
Updates how the user is displayed to neighbors in the micro-forum.
Only the user of that membership may change it. A membership that belongs to someone else returns `404 MEMBERSHIP_NOT_FOUND`, the same as an unknown one, so membership IDs cannot be probed.
**Request:**
```json
{
  "privacy_display_mode": "INITIALS" // "APARTMENT_ONLY" | "INITIALS" | "FULL_NAME"
}
Response 200 OK:
code
JSON
{
  "membership_id": "uuid",
  "privacy_display_mode": "INITIALS",
  "preview_badge": {
    "display_label": "I. P. (Apt. 14)",
    "role_badge": "OWNER"
  }
}
2. Meter Readings & Two-Step Anomaly Flow (meters)
GET /api/v1/meters/my-dashboard?period_code=2026-10
Returns the resident's apartment meters, previous readings, current period status, and deadline.
`period_code` is optional. It defaults to the latest period that is `OPEN_FOR_RESIDENTS`, else the latest period overall, so a pre-created future period is never picked. `period` is `null` when the building has no periods yet. Only active meters of the user's own apartments are listed.
```json
{
  "period": { "period_code": "2026-10", "status": "OPEN_FOR_RESIDENTS", "due_date": "2026-11-04T23:59:59Z" },
  "apartments": [
    {
      "apartment_id": "uuid", "apartment_number": "14", "can_submit_readings": true,
      "meters": [
        {
          "meter_id": "uuid", "resource_type": "COLD_WATER", "serial_number": "SN-1", "location_label": "Bathroom",
          "unit": "m3", "previous_value": "124.000",
          "reading": { "reading_id": "uuid", "value": "128.200", "consumption_delta": "4.200", "source": "RESIDENT", "is_anomaly_confirmed": false }
        }
      ]
    }
  ]
}
```
`reading` is `null` until something was submitted for that period.

POST /api/v1/meters/readings
Submits or updates a meter reading for an open period.
Who may submit, and in which period status:

| Caller | Meter | Allowed period status | Stored `source` |
| :--- | :--- | :--- | :--- |
| Member of the meter's apartment with `can_submit_readings` | apartment meter | `OPEN_FOR_RESIDENTS` (otherwise `409 PERIOD_CLOSED`) | `RESIDENT` |
| `CHAIRMAN` / `ACCOUNTANT` | any meter of the building, including master meters | `OPEN_FOR_RESIDENTS`, `REVIEW_BY_BOARD` (otherwise `409 PERIOD_LOCKED`) | `ADMIN_OVERRIDE` |

- **Reason.** A board change (`ADMIN_OVERRIDE`) to a reading that already exists requires a non-blank `reason` (`422 REASON_REQUIRED`). Blank reasons count as missing.
- **Visibility.** An unknown meter, or one the caller may not see, returns `404 METER_NOT_FOUND`. `BOARD` members see meters in the matrix but get `403 FORBIDDEN` on submit. So does a member without `can_submit_readings`. An unknown period returns `404 PERIOD_NOT_FOUND`.
- **Board corrections stay.** A resident cannot overwrite a reading the board set (`409 READING_SET_BY_BOARD`), even after the period is reopened. Replacing an `AUTO_ESTIMATED` reading is allowed.
- **No rewriting history.** A reading cannot change once a later period already has a reading for that meter (`409 LATER_READING_EXISTS`), because that period's `previous_value` snapshot would silently break.
- **Replaced meters.** An inactive (replaced) meter only accepts board corrections of a reading it already has in that period.
- **No-op.** Re-submitting exactly what is stored is a no-op: no audit row, no event. "Exactly" means the same value, previous value, delta, source, anomaly flag and submitter, and no new `reason`. Otherwise the full path runs, e.g. the board confirming an estimate with the same value.
- `previous_value` is the meter's reading in the latest earlier period, or `initial_value` if there is none.
- `average_delta` follows docs/ROADMAP_MVP.md, section C.
- **Locking.** The period is taken `FOR SHARE`, then the meter `FOR UPDATE`. Status changes and estimates lock the period `FOR NO KEY UPDATE` first, then the meters. This one order means a submission and a status change or estimate run one after the other and never deadlock, and a reading can never land in a period that was just closed.
- `due_date` is informational in the MVP. Periods are closed by `PATCH …/status`, not automatically.
Request:
code
JSON
{
  "meter_id": "uuid",
  "period_code": "2026-10",
  "value": "1254.000",
  "confirmation_level": 0,
  "reason": null
}
Response Scenarios:
422 Unprocessable Entity (Value lower than previous reading):
code
JSON
{
  "error_code": "READING_BELOW_PREVIOUS",
  "previous_value": "124.000",
  "submitted_value": "120.000",
  "message": "New reading cannot be lower than previous reading (124.000)."
}
409 Conflict — Popup #1 (confirmation_level == 0 and delta > threshold):
code
JSON
{
  "error_code": "ANOMALY_CONFIRMATION_STEP_1",
  "required_confirmation_level": 1,
  "consumption_delta": "1130.000",
  "average_delta": "4.200",
  "max_reasonable_delta": "25.000",
  "message": "Value is unusually high (consumption 1130.000 m3), are you sure?"
}
409 Conflict — Popup #2 (confirmation_level == 1 and delta > threshold):
code
JSON
{
  "error_code": "ANOMALY_CONFIRMATION_STEP_2",
  "required_confirmation_level": 2,
  "consumption_delta": "1130.000",
  "message": "Please double-check the digits; this goes directly to billing!"
}
200 OK (Normal delta OR confirmation_level >= 2):
Saves reading, appends row to meters_reading_logs, emits MeterReadingSubmitted once the request has committed.
code
JSON
{
  "reading_id": "uuid",
  "meter_id": "uuid",
  "period_code": "2026-10",
  "value": "1254.000",
  "consumption_delta": "1130.000",
  "source": "RESIDENT",
  "is_anomaly_confirmed": true
}
3. Chairman / Accountant Matrix & Controls (meters)
GET /api/v1/meters/periods/{period_code}/matrix
(Requires role: CHAIRMAN, BOARD, or ACCOUNTANT)
Returns:
Building Master Meters summary (master_total_m3 vs apartments_sum_m3 and unallocated_loss_m3).
List of all apartments with submission status (SUBMITTED, MISSING, AUTO_ESTIMATED, ANOMALY_CONFIRMED).
```json
{
  "period": { "period_code": "2026-10", "status": "REVIEW_BY_BOARD", "due_date": "2026-11-04T23:59:59Z" },
  "balances": [
    { "resource_type": "COLD_WATER", "unit": "m3", "master_total": "52.000", "apartments_sum": "48.500", "unallocated_loss": "3.500" }
  ],
  "master_meters": [
    { "meter_id": "uuid", "resource_type": "COLD_WATER", "location_label": "Basement Master", "unit": "m3",
      "previous_value": "1030.000", "status": "SUBMITTED",
      "reading": { "reading_id": "uuid", "value": "1082.000", "consumption_delta": "52.000", "source": "ADMIN_OVERRIDE", "is_anomaly_confirmed": false } }
  ],
  "apartments": [
    {
      "apartment_id": "uuid", "apartment_number": "14", "entrance": 2, "floor": 4, "status": "ANOMALY_CONFIRMED",
      "meters": [
        { "meter_id": "uuid", "resource_type": "COLD_WATER", "location_label": "Bathroom", "unit": "m3", "status": "ANOMALY_CONFIRMED",
          "reading": { "reading_id": "uuid", "value": "1254.000", "consumption_delta": "1130.000", "source": "RESIDENT", "is_anomaly_confirmed": true } }
      ]
    }
  ]
}
```
- `master_meters` lists the Building Master Meters with their ids, so the board can submit them.
- `balances` has one row per resource type and unit that has master meters (kWh and MWh are never summed together), so the generic names also cover kWh and MWh: `master_total_m3` is `master_total` with `unit` `m3`.
- `master_total` is `null` while any master meter of that type has no reading; then `unallocated_loss` is `null` too.
- `unallocated_loss = master_total - apartments_sum` can be negative, which points to a wrong reading.
- A meter's status is `MISSING` without a reading; otherwise `ANOMALY_CONFIRMED`, `AUTO_ESTIMATED` or `SUBMITTED`.
- An apartment's status is the most urgent of its meters' statuses, in the order `MISSING` > `ANOMALY_CONFIRMED` > `AUTO_ESTIMATED` > `SUBMITTED`.
- Apartments without active meters are not listed. The payload contains no names.
POST /api/v1/meters/periods/{period_code}/estimate-missing
(Requires role: CHAIRMAN or ACCOUNTANT)
Finds all active apartment meters without a reading in period_code, calculates their 3-month rolling average delta, inserts readings with source = "AUTO_ESTIMATED", and writes audit logs.
- Allowed only in `REVIEW_BY_BOARD` (`409 PERIOD_NOT_IN_REVIEW`), so residents can no longer submit.
- The estimated `value` is `previous_value + average`, where the average defaults to `0.000` without history. The reading has `submitted_by_user_id = NULL`; its audit row records the triggering user and the reason `"3-month avg estimate"`.
- Running it again estimates nothing new.
- If `previous + average` would exceed NUMERIC(12, 3), nothing is saved and the response is `422 ESTIMATE_OUT_OF_RANGE` with that `meter_id`; enter that reading by hand.
- Response: `{"period_code": "2026-10", "estimated_count": 2, "estimated": [{"reading_id", "meter_id", "apartment_id", "value", "consumption_delta"}]}`.
PATCH /api/v1/meters/periods/{period_code}/status
Transitions period status (OPEN_FOR_RESIDENTS ↔ REVIEW_BY_BOARD ↔ LOCKED).
- Requires role `CHAIRMAN` or `ACCOUNTANT`.
- Request: `{"status": "REVIEW_BY_BOARD"}`. Response: `{"period_code": "2026-10", "previous_status": "OPEN_FOR_RESIDENTS", "status": "REVIEW_BY_BOARD"}`.
- Allowed transitions:
  - `OPEN_FOR_RESIDENTS → REVIEW_BY_BOARD`
  - `REVIEW_BY_BOARD → OPEN_FOR_RESIDENTS | LOCKED`
  - `LOCKED → REVIEW_BY_BOARD` (reopen before invoices are sent)
- Anything else, including skipping a step or "changing" to the same status, returns `409 INVALID_PERIOD_TRANSITION` with `allowed_statuses`.
- A transition emits `ReadingPeriodStatusChanged`.
GET /api/v1/meters/readings/{reading_id}/history
Returns chronological audit trail from meters_reading_logs.
- Visible to `CHAIRMAN`, `BOARD` and `ACCOUNTANT`, and to members of the meter's apartment. Anyone else gets `404 READING_NOT_FOUND`.
- The actor is shown only as a masked author badge (`null` for the system), never as a name or user id.
- Response: `{"reading_id", "meter_id", "period_code", "entries": [{"changed_at", "old_value", "new_value", "source", "reason", "changed_by": {"display_label", "role_badge"} | null}]}`.

GET /api/v1/meters/periods/{period_code}/export-csv
Executes plugins/common/csv_export and streams a CSV file of the period's readings.
- Requires role `CHAIRMAN`, `BOARD` or `ACCOUNTANT`. Served as `text/csv; charset=utf-8` with `Content-Disposition: attachment; filename="readings-2026-10.csv"`.
- Columns: `period_code, entrance, apartment_number, meter_id, resource_type, serial_number, location_label, unit, previous_value, value, consumption_delta, source, is_anomaly_confirmed, recorded_at`.
- Master meters have an empty apartment, and `MASTER` in `apartment_number`.
- Text cells that would start a spreadsheet formula are prefixed with `'` (CSV-injection guard). That covers `= + - @`, also after leading whitespace or line breaks and in full-width forms (NFKC), and a leading tab, CR or LF. No names.
- If the CSV plugin is not enabled (`ENABLED_PLUGINS`), the response is `404 EXPORTER_NOT_ENABLED`.
## 4. Emergency Alerts & Community Forum (`communication`)

### Who sees which post
- Every post belongs to one building. Users of another building never see it: `404 POST_NOT_FOUND`.
- **Targeting** (`target_scope`): `ALL_BUILDING` reaches everyone in the building; `ENTRANCE` reaches users with an apartment in `target_entrance`; `APARTMENT_LIST` reaches members of the apartments in `target_apartment_ids`. A user with several apartments sees the posts targeted at any of them.
- `CHAIRMAN` and `BOARD` see every post of the building, including alerts targeted elsewhere (they publish and resolve them). `ACCOUNTANT` and `USER` see only what targets them, so an accountant without an apartment sees only `ALL_BUILDING` posts.
- A post that exists but does not target you returns the same `404 POST_NOT_FOUND` as an unknown one, so ids cannot be probed.
- Forum topics are always `ALL_BUILDING`. Targeting is meant for `EMERGENCY` and `OFFICIAL_NOTICE`.

### Who may publish
| `post_type` | Who | Badge context when `author_apartment_id` is omitted |
| :--- | :--- | :--- |
| `EMERGENCY` | `CHAIRMAN`, `BOARD` | none: the board speaks as the board (`Chairman` / `Board`) |
| `OFFICIAL_NOTICE` | `CHAIRMAN`, `BOARD` | none |
| `FORUM_TOPIC` | everyone in the building | the author's first apartment (entrance, then natural number order) |

Anyone else gets `403 FORBIDDEN`. Comments use the commenter's first apartment unless `author_apartment_id` says otherwise.

### Author badges
- Authors are resolved on every read through `PropertyService.get_masked_author_badges` (one batched call per page), following the badge rules in section 0. So a later change of the privacy mode also changes the badge of old posts.
- `author_apartment_id` (optional on posts and comments) must be one of the caller's own apartments, else `422 INVALID_AUTHOR_APARTMENT`. It only chooses which membership's privacy mode applies.
- Payloads never contain an author's user id, name, email or phone. `is_mine` tells the caller which posts and comments are their own. The frontend renders a badge as `"<display_label> • <role_badge>"`, e.g. `"Apt. 14 • OWNER"`.

### Free text
`title` (1..255 characters), post `body` (1..10000) and comment `body` (1..4000) use the safe-text types: they are trimmed first, then must not be blank and must fit the limit, and they reject control characters such as NUL (`422 VALIDATION_ERROR`). Tab, LF and CR are allowed.

### Post object (`PostView`)
```json
{
  "id": "uuid",
  "post_type": "FORUM_TOPIC",
  "category": "GENERAL",
  "title": "Let's install a bike rack in the yard",
  "body": "Neighbours, what do you think?",
  "author_badge": { "display_label": "Apt. 14", "role_badge": "OWNER" },
  "is_mine": false,
  "target_scope": "ALL_BUILDING",
  "target_entrance": null,
  "target_apartment_ids": null,
  "is_active": true,
  "comments_enabled": true,
  "comments_count": 3,
  "created_at": "2026-10-25T10:00:00Z"
}
```
- `post_type`: `EMERGENCY` | `OFFICIAL_NOTICE` | `FORUM_TOPIC`. `category`: `GENERAL` | `WATER_SHUTOFF` | `MAINTENANCE` | `MARKETPLACE`.
- `is_active` is `false` only for a resolved emergency; every other post is always active.

### `GET /api/v1/communication/emergencies/active`
Active `EMERGENCY` posts that target the caller (see "Who sees which post"), newest first. Each item is a post object plus `acknowledged_at`: when the caller acknowledged it, or `null`. The UI shows the sticky banner for items with `acknowledged_at: null`.

### `POST /api/v1/communication/emergencies/{post_id}/acknowledge`
Records that the caller saw the alert ("Acknowledge" hides the banner for that user only).
- Idempotent: acknowledging again keeps and returns the first time. Works on resolved alerts too.
- Only for an emergency visible to the caller; anything else is `404 POST_NOT_FOUND`.
- Response: `{"post_id": "uuid", "acknowledged_at": "2026-10-25T10:05:00Z"}`.

### `POST /api/v1/communication/emergencies/{post_id}/resolve`
(Requires role: `CHAIRMAN` or `BOARD`)
Ends an emergency: `is_active` becomes `false`, so the banner disappears for everyone. The post stays readable in the feed.
- Idempotent: resolving again returns the same response. Only the call that actually ends it emits `EmergencyAlertResolved`.
- Not an emergency of the caller's building: `404 POST_NOT_FOUND`.
- Response: `{"post_id": "uuid", "is_active": false}`.

### `GET /api/v1/communication/posts?post_type=FORUM_TOPIC&category=GENERAL&limit=50&offset=0`
The feed: posts visible to the caller, newest first (`created_at`, then `id`), as a list of post objects.
- `post_type` and `category` are optional filters; without `post_type` the feed contains every type, including resolved emergencies.
- `limit` 1..100 (default 50), `offset` 0..100000 (default 0).

### `POST /api/v1/communication/posts`
Creates a post. Response `201 Created` with the post object.
**Request:**
```json
{
  "post_type": "EMERGENCY",
  "category": "WATER_SHUTOFF",
  "title": "Cold water off until 18:00",
  "body": "A riser in entrance 2 is being replaced.",
  "target_scope": "ENTRANCE",
  "target_entrance": 2,
  "target_apartment_ids": null,
  "comments_enabled": true,
  "author_apartment_id": null
}
```
- Defaults: `category` `GENERAL`, `target_scope` `ALL_BUILDING`, `comments_enabled` `true`. Unknown fields are rejected.
- **Target rules** (`422 INVALID_TARGET`, with a `message` that names the problem):
  - `ALL_BUILDING` takes neither `target_entrance` nor `target_apartment_ids`;
  - `ENTRANCE` needs `target_entrance`, and the building must have an apartment in that entrance;
  - `APARTMENT_LIST` needs 1..500 different apartment ids (duplicates are dropped before counting), all in the caller's building; unknown ones are listed in `unknown_apartment_ids`. An empty list counts as not given;
  - a `FORUM_TOPIC` must be `ALL_BUILDING`.
- Creating an `EMERGENCY` emits `EmergencyAlertPublished` after the request commits.

### `GET /api/v1/communication/posts/{post_id}?comments_limit=200&comments_offset=0`
The post object plus one page of `comments`, oldest first: `[{"id", "post_id", "body", "author_badge", "is_mine", "created_at"}]`. `comments_limit` 1..500 (default 200), `comments_offset` 0..100000; `comments_count` is always the total. A post not visible to the caller is `404 POST_NOT_FOUND`.

### `DELETE /api/v1/communication/posts/{post_id}`
(Requires role: `CHAIRMAN`)
Moderation: deletes a forum topic together with its comments, for everyone. Response `204 No Content`.
- A soft delete: the row keeps `deleted_at` and `deleted_by_user_id` for disputes, but every read treats the topic as gone (`404 POST_NOT_FOUND`, also for the chairman and for a second delete).
- Only `FORUM_TOPIC` posts: an emergency or an official notice returns `409 POST_NOT_DELETABLE` with its `post_type` (an emergency is resolved instead).
- A post of another building, or an unknown one, is `404 POST_NOT_FOUND`.

### `POST /api/v1/communication/posts/{post_id}/comments`
Request: `{"body": "Good idea!", "author_apartment_id": null}`. Response `201 Created` with the comment object.
- The post must be visible to the caller (`404 POST_NOT_FOUND`) and have `comments_enabled` (`409 COMMENTS_DISABLED`).

### Domain events
Published on the event bus **after** the request's transaction commits (never for a rolled-back change). Payloads carry ids, codes and the target only, no titles, bodies or names; a notifier reads the text through `CommunicationService`.
- `EmergencyAlertPublished`: `post_id`, `building_id`, `category`, `target_scope`, `target_entrance`, `target_apartment_ids`, `published_by_user_id`.
- `EmergencyAlertResolved`: `post_id`, `building_id`, `resolved_by_user_id`.

Further moderation (authors deleting their own posts, hiding single comments, reports, a moderation log) is in the Phase 2 backlog, docs/ROADMAP_MVP.md.

## 5. Demo mode (`demo`)

### `GET /api/v1/demo/users`
The users of the demo role switcher. **No authentication**, because the switcher needs the list before anyone acts as a user. Only while `DEMO_MODE_ENABLED=true`; otherwise `404 NOT_FOUND`, like an unknown route.
```json
[
  { "user_id": "uuid", "label": "Owner Apt 14", "building_role": "USER", "apartment_number": "14", "relation_type": "OWNER" }
]
```
- Send `user_id` as `X-Demo-User-Id` to act as that user.
- Lists only the seeded demo users that exist and are active (an empty list on an instance without demo data). No names, emails or phones.
- The ids are fixed (the same on every instance), but clients should read them from this endpoint.

### Demo building "KÜ Sõpruse 12"
Seeded at startup by `src/core/seed.py` when `SEED_DEMO_DATA=true` (local Docker and the public demo), and only into an **empty** database: an instance that already has a building is never touched, and a restart changes nothing.

| Label | Role | Apartment | Privacy mode | Shown in the forum as |
| :--- | :--- | :--- | :--- | :--- |
| Chairman | `CHAIRMAN` | none | | `Chairman` (`BOARD`) |
| Board member (Apt 15) | `BOARD` | 15, owner | `FULL_NAME` | `Peeter Saar (Apt. 15)` (`BOARD`) |
| Accountant | `ACCOUNTANT` | none | | `Accountant` (`ACCOUNTANT`) |
| Owner Apt 14 | `USER` | 14, owner | `APARTMENT_ONLY` | `Apt. 14` (`OWNER`) |
| Owner Apt 1 | `USER` | 1, owner | `FULL_NAME` | `Kati Mets (Apt. 1)` (`OWNER`) |
| Tenant Apt 2 | `USER` | 2, tenant | `INITIALS` | `M. T. (Apt. 2)` (`RESIDENT`) |

- **Building:** entrance 1 has apartments 1, 2, 3; entrance 2 has 13, 14, 15. Apartments 3 and 13 have no app users. Every apartment has a cold and a hot water meter; the basement has a cold and a hot water master meter.
- **History:** `2026-07`, `2026-08`, `2026-09` are `LOCKED` with readings for every meter. Readings of apartments without app users were entered by the chairman (`ADMIN_OVERRIDE`); Apt 13 in `2026-08` is `AUTO_ESTIMATED`. The master meters show a small loss over the apartments. Apt 14's cold water reading of `2026-09` was corrected once, so its history has two entries.
- **October (`2026-10`, `OPEN_FOR_RESIDENTS`):** Apt 1 submitted both meters and Apt 2 its cold water. Apt 15's hot water is a confirmed anomaly (31 m³, a leaking tap). **Apt 14 has submitted nothing**, so the demo can start there; a normal value is its previous value plus about 4.8 m³ (cold) or 2.2 m³ (hot). A delta above 3 × its 3-month average (about 14.4 m³ cold, 6.5 m³ hot) or above the meter limit (25 / 15 m³) triggers the two popups.
- **Communication:** an active `EMERGENCY` (hot water off) targeted at entrance 2, so Apt 14 sees the banner and Apt 2 does not; an `OFFICIAL_NOTICE` without comments; two forum topics with comments from several residents.
- Seeding publishes no domain events, so no notifications are sent.
