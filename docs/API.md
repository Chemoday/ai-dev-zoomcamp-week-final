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

Every `/api/v1` endpoint requires an authenticated user, except `GET /api/v1/demo/users` (section 5), the Telegram webhook (section 6, called by Telegram with its own secret) and the sign-in endpoints of section 7 *(planned, E7)*:
- `Authorization: Bearer <jwt>`: HS256 token with `sub = <user uuid>` and `exp`. It is accepted only when `JWT_SECRET_KEY` is configured. *(Planned, E7:)* the key becomes required; the app issues the token at sign-in (section 7) with the claims `sid` and `auth_time` and a lifetime of 15 minutes, and rejects a token whose session was revoked. Tokens without `sid` (minted by tests) are accepted only with `AUTH_ALLOW_SESSIONLESS_TOKENS=true`.
- `X-Demo-User-Id: <user uuid>`: accepted only when `DEMO_MODE_ENABLED=true` (local Docker, tests and the public demo). If both headers are sent, the Bearer token wins. It never authenticates the account endpoints of section 7.

All errors share one JSON shape, `{"error_code": "...", "message": "...", ...extra}`:

| Status | `error_code` | When |
| :--- | :--- | :--- |
| 401 | `NOT_AUTHENTICATED` / `INVALID_TOKEN` / `UNKNOWN_USER` | No credentials, a bad or expired token, or an unknown or inactive user (sent with `WWW-Authenticate: Bearer`) |
| 400 | `INVALID_LINK` *(planned, E7)* | A sign-in or invitation link that is unknown, used, expired or no longer applies (section 7) |
| 403 | `FORBIDDEN` | Authenticated, but the building role or a membership flag (e.g. `can_submit_readings`) does not allow it |
| 404 | `NOT_FOUND`, `MEMBERSHIP_NOT_FOUND`, `METER_NOT_FOUND`, `PERIOD_NOT_FOUND`, `READING_NOT_FOUND`, `POST_NOT_FOUND`, `NOTIFICATION_NOT_FOUND`, `EXPORTER_NOT_ENABLED` | Unknown route, a resource that does not exist or is not visible to you, or a disabled plugin |
| 409 | `ANOMALY_CONFIRMATION_STEP_1/2`, `PERIOD_CLOSED`, `PERIOD_LOCKED`, `PERIOD_NOT_IN_REVIEW`, `INVALID_PERIOD_TRANSITION`, `READING_SET_BY_BOARD`, `LATER_READING_EXISTS`, `COMMENTS_DISABLED`, `POST_NOT_DELETABLE` | The request is valid, but conflicts with the current state |
| 405 | `METHOD_NOT_ALLOWED` | The route exists but not for this HTTP method |
| 422 | `VALIDATION_ERROR`, `READING_BELOW_PREVIOUS`, `REASON_REQUIRED`, `ESTIMATE_OUT_OF_RANGE`, `INVALID_TARGET`, `INVALID_AUTHOR_APARTMENT`, `PREFERENCE_MANDATORY` | Invalid input; for `VALIDATION_ERROR` the details are in `errors`. Free-text fields reject control characters, e.g. NUL |
| 500 | `INTERNAL_SERVER_ERROR` | Unhandled error; the body carries `trace_id` |
| other | `HTTP_<status>` | Any other framework-level HTTP error |

*(Planned, E7:)* section 7 adds `INVALID_CREDENTIALS`, `SESSION_EXPIRED` (401), `CSRF_CHECK_FAILED`, `REAUTHENTICATION_REQUIRED` (403), `SESSION_NOT_FOUND`, `INVITATION_NOT_FOUND`, `APARTMENT_NOT_FOUND`, `USER_NOT_FOUND` (404), `EMAIL_ALREADY_REGISTERED`, `TENANT_LIMIT_REACHED`, `INVITATION_ALREADY_PENDING`, `INVITATION_ACCEPTED`, `LAST_CHAIRMAN` (409) and `PASSWORD_TOO_WEAK` (422); it reuses `RATE_LIMITED` (429) of section 6.

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
The payload also has `"locale": "et" | "ru" | "en" | null`: the language of notifications sent outside the app (`null` = the instance default). It is not personal data.

*(Planned, E7:)* ended memberships are not listed. Memberships are ordered by entrance, then apartment number in natural order (`2`, `10`, `14`, `14A`). The top-level `privacy_display_mode` is the most private mode across the memberships (`APARTMENT_ONLY` when there are none); it is the mode used when the user acts without an apartment context. Only memberships of apartments in the user's own building are returned.

### `GET /api/v1/property/building`
The association and its building, for the app header and the dashboard. Any user of the building.
```json
{
  "building_id": "uuid",
  "name": "KÜ Sõpruse 12",
  "registry_code": "80123456",
  "address": "Sõpruse pst 12, Kristiine, 10615 Tallinn",
  "country_code": "EE",
  "reading_deadline_day": 4,
  "apartment_count": 6,
  "entrance_count": 2,
  "heated_area_m2": "329.20"
}
```
- `registry_code` is the association's code in the commercial register, `null` until it is entered.
- `apartment_count`, `entrance_count` and `heated_area_m2` are computed from the apartments. `heated_area_m2` is the sum of the apartments' `area_m2`, a decimal string with 2 places (`"0.00"` without apartments); common areas are not included.
- No people in the payload: no chairman name, no contact details.

### `PATCH /api/v1/property/memberships/{membership_id}/privacy`
Updates how the user is displayed to neighbors in the micro-forum.
Only the user of that membership may change it. A membership that belongs to someone else returns `404 MEMBERSHIP_NOT_FOUND`, the same as an unknown one, so membership IDs cannot be probed.
**Request:**
```json
{
  "privacy_display_mode": "INITIALS" // "APARTMENT_ONLY" | "INITIALS" | "FULL_NAME"
}
```
**Response 200 OK:**
```json
{
  "membership_id": "uuid",
  "privacy_display_mode": "INITIALS",
  "preview_badge": {
    "display_label": "I. P. (Apt. 14)",
    "role_badge": "OWNER"
  }
}
```

## 2. Meter Readings & Two-Step Anomaly Flow (`meters`)

### `GET /api/v1/meters/my-dashboard?period_code=2026-10`
Returns the resident's apartment meters, previous readings, current period status, and deadline.
`period_code` is optional. It defaults to the latest period that is `OPEN_FOR_RESIDENTS`, else the latest period overall, so a pre-created future period is never picked. `period` is `null` when the building has no periods yet. Only active meters of the user's own apartments are listed.
```json
{
  "period": { "period_code": "2026-10", "status": "OPEN_FOR_RESIDENTS", "due_date": "2026-11-04T21:59:59Z" },
  "apartments": [
    {
      "apartment_id": "uuid", "apartment_number": "14", "can_submit_readings": true,
      "meters": [
        {
          "meter_id": "uuid", "resource_type": "COLD_WATER", "serial_number": "SN-1", "location_label": "Bathroom",
          "unit": "m3", "previous_value": "124.000", "average_delta": "4.200", "previous_delta": "3.900",
          "reading": { "reading_id": "uuid", "value": "128.200", "consumption_delta": "4.200", "source": "RESIDENT", "is_anomaly_confirmed": false }
        }
      ]
    }
  ]
}
```
`reading` is `null` until something was submitted for that period.

Consumption hints, so the client can show "your usual consumption is about 4.2 m³" before anything is submitted:
- `average_delta`: the meter's 3-period average before this period, exactly the value the anomaly check uses (docs/ROADMAP_MVP.md, section C: only `RESIDENT`, `ADMIN_OVERRIDE` and `IOT_WEBHOOK` readings that are not confirmed anomalies count). `null` when there is no such history.
- `previous_delta`: the consumption of the latest earlier period that has a reading for the meter (any source). `null` when there is none.
- Both are `null` when `period` is `null`. There is no comparison with the rest of the building: other apartments' consumption is not shown to residents.

### `GET /api/v1/meters/periods`
The reading periods of the user's building, newest first, for a period selector and the archive of exports. Any user of the building.
```json
{
  "periods": [
    { "period_code": "2026-10", "status": "OPEN_FOR_RESIDENTS", "due_date": "2026-11-04T21:59:59Z" },
    { "period_code": "2026-09", "status": "LOCKED", "due_date": "2026-10-04T20:59:59Z" }
  ]
}
```
An empty list when the building has no periods yet. Matrix and export stay limited to the staff roles.

### `POST /api/v1/meters/readings`
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
- `due_date` is the end of the local deadline day in `INSTANCE_TIMEZONE` (default `Europe/Tallinn`), sent in UTC: `2026-11-04T21:59:59Z` is 23:59:59 on 4 November in Tallinn. Periods are closed by `PATCH …/status`, not automatically; `due_date` only drives the reading reminders (section 6).

**Request:**
```json
{
  "meter_id": "uuid",
  "period_code": "2026-10",
  "value": "1254.000",
  "confirmation_level": 0,
  "reason": null
}
```
**Response scenarios:**

`422 Unprocessable Entity` (value lower than the previous reading):
```json
{
  "error_code": "READING_BELOW_PREVIOUS",
  "previous_value": "124.000",
  "submitted_value": "120.000",
  "message": "New reading cannot be lower than previous reading (124.000)."
}
```
`409 Conflict`, popup #1 (`confirmation_level == 0` and delta > threshold):
```json
{
  "error_code": "ANOMALY_CONFIRMATION_STEP_1",
  "required_confirmation_level": 1,
  "consumption_delta": "1130.000",
  "average_delta": "4.200",
  "max_reasonable_delta": "25.000",
  "message": "Value is unusually high (consumption 1130.000 m3), are you sure?"
}
```
`409 Conflict`, popup #2 (`confirmation_level == 1` and delta > threshold):
```json
{
  "error_code": "ANOMALY_CONFIRMATION_STEP_2",
  "required_confirmation_level": 2,
  "consumption_delta": "1130.000",
  "message": "Please double-check the digits; this goes directly to billing!"
}
```
`200 OK` (normal delta, or `confirmation_level >= 2`): saves the reading, appends a row to `meters_reading_logs`, and emits `MeterReadingSubmitted` once the request has committed.
```json
{
  "reading_id": "uuid",
  "meter_id": "uuid",
  "period_code": "2026-10",
  "value": "1254.000",
  "consumption_delta": "1130.000",
  "source": "RESIDENT",
  "is_anomaly_confirmed": true
}
```

## 3. Chairman / Accountant Matrix & Controls (`meters`)

### `GET /api/v1/meters/periods/{period_code}/matrix`
(Requires role: `CHAIRMAN`, `BOARD` or `ACCOUNTANT`)

Returns the Building Master Meters summary (`master_total_m3` vs `apartments_sum_m3` and `unallocated_loss_m3`) and the list of all apartments with their submission status (`SUBMITTED`, `MISSING`, `AUTO_ESTIMATED`, `ANOMALY_CONFIRMED`).
```json
{
  "period": { "period_code": "2026-10", "status": "REVIEW_BY_BOARD", "due_date": "2026-11-04T21:59:59Z" },
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

### `POST /api/v1/meters/periods/{period_code}/estimate-missing`
(Requires role: `CHAIRMAN` or `ACCOUNTANT`)

Finds all active apartment meters without a reading in period_code, calculates their 3-month rolling average delta, inserts readings with source = "AUTO_ESTIMATED", and writes audit logs.
- Allowed only in `REVIEW_BY_BOARD` (`409 PERIOD_NOT_IN_REVIEW`), so residents can no longer submit.
- The estimated `value` is `previous_value + average`, where the average defaults to `0.000` without history. The reading has `submitted_by_user_id = NULL`; its audit row records the triggering user and the reason `"3-month avg estimate"`.
- Running it again estimates nothing new.
- If `previous + average` would exceed NUMERIC(12, 3), nothing is saved and the response is `422 ESTIMATE_OUT_OF_RANGE` with that `meter_id`; enter that reading by hand.
- Response: `{"period_code": "2026-10", "estimated_count": 2, "estimated": [{"reading_id", "meter_id", "apartment_id", "value", "consumption_delta"}]}`.

### `PATCH /api/v1/meters/periods/{period_code}/status`
Transitions period status (OPEN_FOR_RESIDENTS ↔ REVIEW_BY_BOARD ↔ LOCKED).
- Requires role `CHAIRMAN` or `ACCOUNTANT`.
- Request: `{"status": "REVIEW_BY_BOARD"}`. Response: `{"period_code": "2026-10", "previous_status": "OPEN_FOR_RESIDENTS", "status": "REVIEW_BY_BOARD"}`.
- Allowed transitions:
  - `OPEN_FOR_RESIDENTS → REVIEW_BY_BOARD`
  - `REVIEW_BY_BOARD → OPEN_FOR_RESIDENTS | LOCKED`
  - `LOCKED → REVIEW_BY_BOARD` (reopen before invoices are sent)
- Anything else, including skipping a step or "changing" to the same status, returns `409 INVALID_PERIOD_TRANSITION` with `allowed_statuses`.
- A transition emits `ReadingPeriodStatusChanged`.

### `GET /api/v1/meters/readings/{reading_id}/history`
Returns chronological audit trail from meters_reading_logs.
- Visible to `CHAIRMAN`, `BOARD` and `ACCOUNTANT`, and to members of the meter's apartment. Anyone else gets `404 READING_NOT_FOUND`.
- The actor is shown only as a masked author badge (`null` for the system), never as a name or user id.
- Response: `{"reading_id", "meter_id", "period_code", "entries": [{"changed_at", "old_value", "new_value", "source", "reason", "changed_by": {"display_label", "role_badge"} | null}]}`.

### `GET /api/v1/meters/periods/{period_code}/export-csv`
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

- **Association:** `KÜ Sõpruse 12`, registry code `80123456`, `Sõpruse pst 12, Kristiine, 10615 Tallinn`. An instance seeded before the registry code existed gets the code and the address on its next start (only the demo building, only while its `registry_code` is empty).
- **Building:** entrance 1 has apartments 1, 2, 3; entrance 2 has 13, 14, 15. Apartments 3 and 13 have no app users. Every apartment has a cold and a hot water meter; the basement has a cold and a hot water master meter.
- **History:** `2026-07`, `2026-08`, `2026-09` are `LOCKED` with readings for every meter. Readings of apartments without app users were entered by the chairman (`ADMIN_OVERRIDE`); Apt 13 in `2026-08` is `AUTO_ESTIMATED`. The master meters show a small loss over the apartments. Apt 14's cold water reading of `2026-09` was corrected once, so its history has two entries.
- **October (`2026-10`, `OPEN_FOR_RESIDENTS`):** Apt 1 submitted both meters and Apt 2 its cold water. Apt 15's hot water is a confirmed anomaly (31 m³, a leaking tap). **Apt 14 has submitted nothing**, so the demo can start there; a normal value is its previous value plus about 4.8 m³ (cold) or 2.2 m³ (hot). A delta above 3 × its 3-month average (about 14.4 m³ cold, 6.5 m³ hot) or above the meter limit (25 / 15 m³) triggers the two popups.
- **Communication:** an active `EMERGENCY` (hot water off) targeted at entrance 2, so Apt 14 sees the banner and Apt 2 does not; an `OFFICIAL_NOTICE` without comments; two forum topics with comments from several residents.
- Seeding publishes and stores no domain events, so no notifications are sent, now or later.

## 6. Notifications (`notifications`), epic E1

> **Status.** Implemented (in `docs/openapi.json`): in-app notifications for emergencies,
> resolved emergencies and periods in review; the feed, preferences and the notification language
> (N2); e-mail (N3); Telegram linking, the webhook and the bindings API (N4); WebPush (N5); reading
> reminders and the deletion of old notifications (N6). **Planned** (marked below; not in
> `docs/openapi.json` yet): push to the native phone app (N7). Every current environment runs in demo mode, so only `in_app` delivers
> there and the other channels are reported unavailable. Design: [docs/epics/E1_NOTIFICATIONS.md](epics/E1_NOTIFICATIONS.md).

### How notifications reach people
- Notifications are created from domain events: a published or resolved emergency, a reading
  deadline that is close, a period sent to review. Nobody creates them through the API.
- **Channels:** `in_app` (the feed below, always available), `email` (when the user has an
  e-mail address), `telegram`, `webpush` and *(planned, N7)* `mobile_push` (after linking, see
  below). External channels are plugins; a channel whose plugin is not in `ENABLED_PLUGINS` is
  listed as unavailable.
- **How the client learns about new notifications:** it polls `GET …/unread-count` (every 60 s
  while the tab is visible; details in docs/FRONTEND_GUIDE.md section 11). There is no WebSocket or
  server-sent events channel (owner decision 2026-10-31). A user whose app is closed is reached by
  the external channels.
- **Texts:** the in-app feed returns codes and parameters, and the frontend renders localized
  texts by `event_type` (like it does for `error_code`). On e-mail, Telegram and push an emergency
  is the post's title and body as the board wrote them; everything else (subject lines, "resolved",
  reminders, "period in review", the target line and a fixed footer with a link to the site) is a
  fixed system text in the user's `locale`. The chairman cannot change system texts. What leaves
  the system on external channels: the event type, the emergency title and body (written by the
  board), the target (e.g. "entrance 2"), the period code and due date, and a link to the site.
  Comments, forum posts and author names never do.
- **Who gets what:**

| `event_type` | Recipients | Mandatory |
| :--- | :--- | :--- |
| `EMERGENCY_PUBLISHED` | active users of the targeted apartments, plus the chairman and board members; one notification per person and channel; never the author | yes |
| `EMERGENCY_RESOLVED` | one per `EMERGENCY_PUBLISHED` notification that was sent (same channel and device), filtered by preferences; never the person who resolved it | no |
| `READING_DEADLINE` | active members with `can_submit_readings` of apartments where at least one active meter has no reading in the open period; sent between 09:00 and 21:00 local time, starting `READING_REMINDER_DAYS_BEFORE` (default 2) days before the deadline day; if that window is missed, the next window before `due_date` is used, and a message that would go out at night (a retry, an outage) waits for 09:00 local time, and after `due_date` nothing is sent; once per period: once reminders were stored, members who become reachable later are not reminded | no |
| `PERIOD_REVIEW` | the chairman and accountants when a period moves to `REVIEW_BY_BOARD`, except the person who moved it | no |

- Users who join, link a channel or add a device while an emergency is active see it as the banner
  (`GET /api/v1/communication/emergencies/active`); old alerts are not sent to them on any channel.
  Recipients are resolved when the event is processed, normally seconds after it happened. If an
  `EMERGENCY_PUBLISHED` event is processed more than 1 hour after the alert (e.g. after an outage),
  only `in_app` notifications are created and nothing is sent outside the app; a resolved alert is
  skipped.
- **Demo mode.** While `DEMO_MODE_ENABLED=true` anyone can act as any user, so only `in_app`
  works: e-mail, Telegram, WebPush and mobile push are listed as unavailable (`DEMO_MODE`); linking,
  device registration and the VAPID key return `404 CHANNEL_NOT_ENABLED`; the webhook ignores
  updates (`200 {}`); and nothing is sent outside the app. All current environments (local, dev, prod) run in demo mode, so the frontend
  will see only `in_app` until a real login exists.
- Delivery is **at least once**: after a crash a message may arrive twice, but is not lost.

**Default preferences** (`mandatory` events cannot be switched off):

| `event_type` | `in_app` | `email` | `telegram` | `webpush` |
| :--- | :--- | :--- | :--- | :--- |
| `EMERGENCY_PUBLISHED` (mandatory) | on | on | on | on |
| `EMERGENCY_RESOLVED` | on | off | on | on |
| `READING_DEADLINE` | on | on | on | on |
| `PERIOD_REVIEW` | on | on | off | off |

*(Planned, N7:)* `mobile_push` takes the same defaults as `webpush`.

### Notification object (`NotificationView`)
```json
{
  "id": "uuid",
  "event_type": "EMERGENCY_PUBLISHED",
  "subject_type": "post",
  "subject_id": "uuid",
  "params": { "category": "WATER_SHUTOFF", "target_scope": "ENTRANCE", "target_entrance": 2 },
  "created_at": "2026-10-25T10:00:00Z",
  "read_at": null
}
```
- `subject_type` `post` links to `GET /api/v1/communication/posts/{subject_id}`; `period` links to
  the readings of that period (`params.period_code`).
- `params` by `event_type`: emergencies `category`, `target_scope`, `target_entrance`;
  `READING_DEADLINE` `period_code`, `due_date`; `PERIOD_REVIEW` `period_code`.
- Contains no names, titles or bodies; the frontend loads the post if it needs the title.

### `GET /api/v1/notifications/feed?unread_only=false&limit=50&offset=0`
The caller's in-app notifications as a JSON array of `NotificationView`, newest first
(`created_at`, then `id`). `limit` 1..100
(default 50), `offset` 0..100000. Notifications older than `NOTIFICATIONS_RETENTION_DAYS`
(default 180) are deleted.

### `GET /api/v1/notifications/unread-count`
`{"unread": 3}`, for the badge on the bell icon. This is the endpoint the client polls (every 60 s
while the tab is visible, plus once when it becomes visible again); it is one indexed count, so
polling is cheap.

### `POST /api/v1/notifications/feed/{notification_id}/read`
Marks one notification read. Idempotent: the first `read_at` is kept. Response
`{"id": "uuid", "read_at": "2026-10-25T10:05:00Z"}`. Someone else's or unknown id:
`404 NOTIFICATION_NOT_FOUND`.

### `POST /api/v1/notifications/feed/read-all`
Marks all the caller's unread notifications read. Response `{"updated": 3}`.

### `GET /api/v1/notifications/preferences`
The full matrix with defaults filled in, plus which channels can reach the caller.
```json
{
  "channels": [
    { "channel": "in_app", "available": true, "reason": null },
    { "channel": "email", "available": true },
    { "channel": "telegram", "available": false, "reason": "NOT_LINKED" },
    { "channel": "webpush", "available": false, "reason": "CHANNEL_NOT_ENABLED" }
  ],
  "preferences": [
    { "event_type": "EMERGENCY_PUBLISHED", "channel": "email", "enabled": true, "mandatory": true }
  ]
}
```
- `reason` when not available, first match wins: `DEMO_MODE`, `CHANNEL_NOT_ENABLED`, then `NO_EMAIL`
  (e-mail) or `NOT_LINKED` (Telegram, WebPush, mobile push).
- `preferences` always has all 16 combinations of `event_type` and `channel` (20 once
  `mobile_push` exists, N7).

### `PUT /api/v1/notifications/preferences`
Request: `{"preferences": [{"event_type": "PERIOD_REVIEW", "channel": "telegram", "enabled": true}]}`.
Only the listed combinations change. Response: the same body as the `GET`.
- All or nothing, in one transaction. An empty list changes nothing.
- The same combination twice in one request: `422 VALIDATION_ERROR`.
- Switching off a mandatory combination: `422 PREFERENCE_MANDATORY` (nothing is changed);
  `enabled: true` on it is accepted and changes nothing.
- A value equal to the default removes the stored row (only deviations are stored).
- A preference for an unavailable channel can be stored; it applies once the channel is linked.

### `PATCH /api/v1/property/me/locale`
(Module `property`.) Request `{"locale": "ru"}`; `"et"`, `"ru"`, `"en"` or `null` for the instance
default. Response `{"locale": "ru"}`. Anything else: `422 VALIDATION_ERROR`.

### `GET /api/v1/notifications/bindings`
The caller's active bindings: `[{"binding_id": "uuid", "channel": "webpush", "label": "Chrome on
Android", "created_at": "..."}]`. Chat ids, endpoints and FCM tokens are never returned.

### `DELETE /api/v1/notifications/bindings/{binding_id}`
Unlinks a chat or device (`revoked_at` is set, the stored address is wiped). Response
`204 No Content`; queued messages for it are skipped. Someone else's, unknown or already revoked:
`404 BINDING_NOT_FOUND`. **The client deletes its own WebPush binding on logout** and also calls
`PushSubscription.unsubscribe()`, so a shared browser stops receiving the previous user's alerts even
if the token has already expired (the push service then answers 410 and the binding is revoked).
The phone app does the same with its mobile push binding and also deletes its FCM token
(`deleteToken`).

### Telegram linking
#### `POST /api/v1/notifications/telegram/link-code`
Response `201 Created`:
```json
{ "code": "K7Q2M9XA", "expires_at": "2026-10-25T10:10:00Z", "deep_link": "https://t.me/<bot_username>?start=K7Q2M9XA" }
```
- 8 characters of Crockford base32 (`0-9`, `A-Z` without `I L O U`), case-insensitive; valid
  10 minutes, single use. The code is shown once; only its hash is stored.
- At most 5 codes per user per hour: `429 RATE_LIMITED` with `retry_after_seconds` and a
  `Retry-After` header.
- Plugin `common.telegram` not enabled: `404 CHANNEL_NOT_ENABLED`.
- The user opens the deep link (or sends `/start <code>` to the bot). On success the bot replies
  and the binding appears in `GET …/bindings` with the label `Telegram`. The bot's replies
  ("linked", "already linked", "wrong, used or expired code") are system texts in the user's
  language (the instance default for a wrong code); group chats and chats over the attempt limit
  get no reply at all. A bare `/start` (the bot opened without the link) gets a hint to create a
  code on the site and does not count as an attempt.
- **One Telegram chat per user.** Redeeming a code runs in one transaction that takes advisory
  locks in a fixed order, first the user, then the chat, so two chats redeeming codes of the same
  user at once are serialized. A unique-index conflict that still happens becomes a bot reply, and
  the webhook still answers `200`.
  - the chat is already linked to this user: the code is used up, the binding stays, the bot
    replies "already linked";
  - the user has another chat linked: that binding is revoked, the new chat is linked;
  - the chat is linked to another user: that binding is revoked and the chat moves to this user
    (the same rule as WebPush).

#### `POST /api/v1/notifications/telegram/webhook`
Called by Telegram, **not by the frontend**; no user authentication.
- Accepted only with the header `X-Telegram-Bot-Api-Secret-Token` equal to
  `TELEGRAM_WEBHOOK_SECRET` (constant-time comparison). Missing header, wrong secret or disabled
  plugin: `404 NOT_FOUND`, like an unknown route. The app refuses to start with `common.telegram`
  enabled and a secret shorter than 32 characters or outside `[A-Za-z0-9_-]`, so the webhook can
  never be open.
- With a valid secret the response is always `200 {}`, also for ignored updates, so Telegram does
  not retry.
- Body limit 64 KB. Only private chats (`chat.type == "private"`) can link; group chats get no
  binding. At most 10 `/start` attempts per chat per hour; a wrong, used or expired code links
  nothing.
- The webhook is registered with Telegram by an admin command, not on startup:
  `uv run python -m src.plugins.common.telegram set-webhook` (in the backend container) calls
  `setWebhook` with `PUBLIC_BASE_URL` + this path, `secret_token` = `TELEGRAM_WEBHOOK_SECRET` and
  `allowed_updates: ["message"]`; `webhook-info` shows Telegram's view. Dev and prod use different
  bots.

### WebPush
Push to a browser or an installed PWA (RFC 8030), encrypted end to end (RFC 8291, `aes128gcm`):
the push service of the browser vendor sees neither the text nor the link. The server identifies
itself with VAPID (RFC 8292). Client steps: docs/FRONTEND_GUIDE.md section 11.

#### `GET /api/v1/notifications/webpush/vapid-public-key`
`{"public_key": "BEl6..."}` (base64url, 65 bytes) for `pushManager.subscribe`. Plugin
`common.webpush` not enabled, or demo mode: `404 CHANNEL_NOT_ENABLED`. A new key pair (one per
environment) comes from `uv run python -m src.plugins.common.webpush generate-vapid-keys`. After a
key change the push services reject the old subscriptions (`WEBPUSH_AUTH_FAILED`, not revoked: a
configuration error must not wipe every device), so the client compares keys on page load and
subscribes again (docs/FRONTEND_GUIDE.md section 11).

#### `POST /api/v1/notifications/webpush/subscriptions`
Request: the browser's `PushSubscription.toJSON()` plus a label:
```json
{ "endpoint": "https://fcm.googleapis.com/fcm/send/...", "keys": { "p256dh": "...", "auth": "..." }, "label": "Chrome on Android" }
```
- Response `201 Created` with the binding (`binding_id`, `channel`, `label`, `created_at`); the same
  endpoint again for the same user returns `200` with the existing binding, updated with the new
  `keys` and `label`.
- If the endpoint is active for another user, that binding is revoked and the endpoint moves to the
  caller.
- Runs in one transaction under advisory locks, first the user, then the endpoint, so concurrent
  subscriptions end with one active binding per endpoint and never with an error.
- At most 10 active devices per user, checked under the user lock: `409 DEVICE_LIMIT_REACHED`.
- Limits: `endpoint` at most 2048 characters; `keys.p256dh` base64url of 65 bytes (an uncompressed
  P-256 point, first byte `0x04`), `keys.auth` base64url of 16 bytes; otherwise
  `422 VALIDATION_ERROR`. `expirationTime` and unknown fields are ignored.
- Plugin `common.webpush` not enabled, or demo mode: `404 CHANNEL_NOT_ENABLED`.
- **Endpoint check** (`422 INVALID_PUSH_ENDPOINT`): `https` only, no user info, default port, no IP
  address as host, a lower-case host name without punycode labels, and the host must be a known push service (`fcm.googleapis.com`,
  `updates.push.services.mozilla.com`, `*.notify.windows.com`, `web.push.apple.com`; a wildcard
  matches sub-domains only at a dot boundary). `label` 1..100 characters, safe text.
- A push service answering `404` or `410` revokes the binding.
- **What a push carries** (the service worker's `push` event, JSON):
  `{"v": 1, "event_type": "EMERGENCY_PUBLISHED", "title": "...", "body": "...", "url": "<PUBLIC_BASE_URL>", "tag": "post:<id>", "urgent": true}`.
  `title` and `body` are the same texts as on the other external channels, without the e-mail
  footer; a long body is cut to fit one push message (about 4 KB) and then ends with an ellipsis
  (U+2026). `tag` is the same for a post's alert and its "resolved", so the newer notification
  replaces the older.
  Emergencies are sent with `Urgency: high` and expire after 1 hour (`TTL`); everything else is
  `normal` and expires after 24 hours.

### Mobile push *(planned, N7)*
Push to the native phone app (a native app or a wrapper of the Vue client, e.g. Capacitor) through
Firebase Cloud Messaging (FCM); FCM reaches iOS through APNs. A PWA uses WebPush instead. Plugin
`common.fcm_push`, channel `mobile_push`. Design: docs/epics/E1_NOTIFICATIONS.md 6.4.

#### `POST /api/v1/notifications/mobile-push/devices`
Called by the phone app after login, with the FCM registration token of the device:
```json
{ "token": "dQw4w9WgXcQ:APA91b...", "platform": "android", "label": "Pixel 8", "replaces_binding_id": null }
```
- `platform`: `android` or `ios`. On iOS the app sends the **FCM** token (from the Firebase
  Messaging SDK, e.g. `@capacitor-firebase/messaging`; the stock `@capacitor/push-notifications`
  returns the raw APNs device token, which does not work).
- Response `201 Created` with the binding (`binding_id`, `channel`, `label`, `created_at`); the same
  token again for the same user returns `200` with the existing binding and the new `label`.
- If the token is active for another user (a shared phone), that binding is revoked and the token
  moves to the caller. The same advisory lock order as WebPush (user, then token).
- **Token refresh:** FCM may rotate the token. The app registers the new token with
  `replaces_binding_id` set to its previous binding (it keeps `binding_id` locally). That binding is
  revoked in the same transaction before the device limit is checked, so a user at the limit keeps
  receiving pushes. Only the caller's own active `mobile_push` binding is replaced; any other id
  (someone else's, another channel, unknown or already revoked) is ignored, and a malformed UUID is
  `422 VALIDATION_ERROR`.
  A stale token is revoked anyway when FCM answers `UNREGISTERED`.
- At most 10 active mobile devices per user: `409 DEVICE_LIMIT_REACHED`.
- `token` 1..4096 characters of `[A-Za-z0-9_:-]`; `label` 1..100 characters, safe text; otherwise
  `422 VALIDATION_ERROR`. The token is opaque data, never a URL: the server only ever calls the
  fixed FCM host, so no endpoint check is needed.
- Plugin `common.fcm_push` not enabled or demo mode: `404 CHANNEL_NOT_ENABLED`.
- FCM answering `UNREGISTERED` or `SENDER_ID_MISMATCH` revokes the binding.

### Error codes of this section
`NOTIFICATION_NOT_FOUND`, `PREFERENCE_MANDATORY`, `CHANNEL_NOT_ENABLED`, `BINDING_NOT_FOUND`,
`RATE_LIMITED`, `DEVICE_LIMIT_REACHED` and `INVALID_PUSH_ENDPOINT` are all live (mobile push, N7,
reuses `DEVICE_LIMIT_REACHED`).
| Status | `error_code` |
| :--- | :--- |
| 404 | `NOTIFICATION_NOT_FOUND`, `BINDING_NOT_FOUND`, `CHANNEL_NOT_ENABLED` |
| 409 | `DEVICE_LIMIT_REACHED` |
| 422 | `PREFERENCE_MANDATORY`, `INVALID_PUSH_ENDPOINT` |
| 429 | `RATE_LIMITED` (new status; the body has `retry_after_seconds`, plus a `Retry-After` header) |

---

## 7. Login and accounts (`auth`), epic E7 *(in progress)*

> **Status.** Contract of task A1 (issue #64). **Live** (in `docs/openapi.json`):
> - since A2 (#65): `POST …/refresh`, `POST …/logout`, `GET` / `DELETE …/sessions`, and the
>   session check of access tokens;
> - since A3 (#66): signing in with an e-mail link, `POST …/magic-link` and
>   `POST …/magic-link/redeem` (only sign-in links; invitation links come with A4).
>
> Registration, invitations and passwords come with A4-A5. Design and reasons: [docs/epics/E7_LOGIN.md](epics/E7_LOGIN.md). Every
> environment stays in demo mode for now (section 5); these endpoints work there too.

### Rules for every endpoint of this section
- **No demo header.** `X-Demo-User-Id` never authenticates any endpoint of this section.
  Endpoints marked "signed in" need `Authorization: Bearer` from a sign-in, so in demo mode the
  account pages ("My residents", "People", "Security") need a real sign-in, not the role switcher.
- **CSRF:** a request that uses or sets the refresh cookie must send `X-Requested-With` (any
  non-empty value; the frontend sends `fetch`), otherwise `403 CSRF_CHECK_FAILED`. These are:
  - `…/magic-link/redeem`, `…/registration` and `…/password/login`, which set the cookie;
  - `…/refresh`, `…/logout` and `DELETE …/sessions`, which use it.
  The API sends no CORS headers, so other origins can never make credentialed calls.
- **Refresh cookie:** `__Secure-refresh`, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`, no
  `Domain`. It lives 7 days, every refresh extends it, and a session ends after 90 days at the
  latest.
- **E-mail addresses** are matched in canonical form: trimmed, NFKC, lower case. `email` is at
  most 254 characters; otherwise `422 VALIDATION_ERROR`.
- **No answer reveals whether an e-mail has an account,** neither its body, its status nor its
  timing. The only exceptions reach only the holder of an invitation link, i.e. the mailbox
  owner: the redeem answers `REGISTRATION_REQUIRED` / `SIGN_IN_TO_ACCEPT`, and the registration
  `409`.
- **Limits** answer `429 RATE_LIMITED` with `retry_after_seconds` and `Retry-After`, except where
  a limit is silent (marked below). The client IP address comes from `X-Forwarded-For`: Caddy sets
  it and uvicorn trusts it (`--proxy-headers`), and the backend port is never exposed directly.
- **Status 400** is new in this API (`INVALID_LINK`).

### Session object (`SessionResponse`)
Returned when someone signs in; the refresh cookie is set in the same response.
```json
{ "access_token": "eyJ...", "token_type": "Bearer", "expires_in": 900, "session_id": "uuid" }
```
- The access token is a JWT with `sub`, `sid` (the session), `auth_time` (when the person signed
  in, as a UNIX time), `iat`, `exp` and `typ = "access"`, and it lives 15 minutes.
- The client keeps it in memory only.
- A token whose session is revoked, expired or belongs to another user is rejected:
  `401 INVALID_TOKEN`.

### `POST /api/v1/auth/magic-link`
Request `{"email": "ivan@example.com"}`. Response always `202 {"accepted": true}`.
- **The work happens after the response:**
  - for an active account, an e-mail with a one-time sign-in link, valid 15 minutes, goes out;
  - for an e-mail with only pending invitations, each of them is sent again with a new link and
    the same expiry (the old links stop working);
  - otherwise nothing is sent.
- **Silent limits:** 5 per e-mail and IP address together per hour, 20 per e-mail per day, 20 per
  IP address per hour. Over a limit the answer is still `202` and nothing is sent.
  - Counting the e-mail together with the IP address means somebody else's requests cannot use up
    a person's links; the daily cap stops mail bombing.
  - A person who is still signed in is not affected.
- Each link works once; a newer link does not cancel an older one.
- **Demo mode:** sends only with `AUTH_MAIL_IN_DEMO=true` (ARCHITECTURE 4.6).

### `POST /api/v1/auth/magic-link/redeem`
Redeems a link from an e-mail. The client takes `token` from the URL fragment. Request
`{"token": "..."}`. The token must be a sign-in or an invitation token; registration tokens are
refused. Responses `200`:
- **A sign-in link:**
  `{"status": "SIGNED_IN", "session": SessionResponse}`, with the cookie. The link is used up.
- **An invitation, and no account has its e-mail:**
  `{"status": "REGISTRATION_REQUIRED", "registration_token": "...", "expires_in": 1800, "invitation": InvitationPreview}`.
  - No session yet.
  - Each such redeem creates a new registration token and deletes the previous one.
  - The invitation link stays valid until the invitation is accepted, so closing the tab only
    means opening the link again.
- **An invitation, and an account has its e-mail:**
  `{"status": "SIGN_IN_TO_ACCEPT", "invitation": InvitationPreview}`. Nothing is applied and the
  link is not used up. The person signs in the usual way and then accepts with
  `POST /auth/invitations/accept`. An invitation link never creates a session for an existing
  account.
- `InvitationPreview` is `{"kind": "OWNER", "apartment_number": "14", "building_role": null}`.

Errors:
- `400 INVALID_LINK`: unknown, used or expired link, or an invitation that no longer applies. The
  cause is never told.
- `403 CSRF_CHECK_FAILED`.
- `429 RATE_LIMITED`: 20 redeem attempts per IP address per hour.

### `POST /api/v1/auth/registration`
Completes the registration of an invited person and signs them in. Request:
```json
{ "registration_token": "...", "full_name": "Ivan Petrov", "phone": null, "locale": "et", "privacy_display_mode": "APARTMENT_ONLY" }
```
- `full_name`: 1..255 characters, safe text.
- `phone`: optional, at most 50 characters.
- `locale`: `et` / `ru` / `en`.
- `privacy_display_mode`: as in section 1. It is ignored for `STAFF` invitations, which create no
  membership; the default is `APARTMENT_ONLY`.

Response `200 {"status": "SIGNED_IN", "session": SessionResponse}`, with the cookie. In one
transaction it creates the account with the membership or the role, marks the invitation
accepted, and uses up its link.

Errors:
- `400 INVALID_LINK`: the registration token is unknown, used or older than 30 minutes, or the
  invitation no longer applies.
- `409 EMAIL_ALREADY_REGISTERED`: an account with this e-mail was created in the meantime. The
  person signs in and accepts instead. Only the invited person can reach this.
- `422 VALIDATION_ERROR`.

### `POST /api/v1/auth/password/login`
Request `{"email": "...", "password": "..."}`. Response `200 SessionResponse`, with the cookie.
- `401 INVALID_CREDENTIALS` for every failure: unknown e-mail, wrong password, no password set,
  inactive account. The timing is the same for all of them.
- `429 RATE_LIMITED`:
  - after 5 failures within 15 minutes for the e-mail and IP address together, or 30 for the IP
    address, each further attempt waits a growing delay;
  - after 100 failures for an e-mail within 24 hours, the password of that e-mail is refused until
    its owner signs in with an e-mail link once (a flag on the password identity). While refused,
    the answer is the same `401 INVALID_CREDENTIALS` with the same timing, as for any other
    failure.
  - **Accepted residual risk:** the silent daily cap on links (20 per e-mail) means a persistent
    attacker can block a person's new sign-ins for at most a day. A person who is still signed in
    is not affected.

### `PUT /api/v1/auth/password` (signed in)
Sets or changes the password. Request `{"new_password": "...", "current_password": "..."}`.
- `current_password` is needed only when the sign-in (`auth_time`) is older than 10 minutes. A
  token without `auth_time` always needs it.
- `new_password`: 15 to 128 characters after NFKC normalisation, and not on the list of common
  passwords. Otherwise `422 PASSWORD_TOO_WEAK` with `reason`: `TOO_SHORT`, `TOO_LONG` or
  `TOO_COMMON`.
- Response `204 No Content`. All other sessions of the user are revoked, and a "your password was
  changed" e-mail goes out.
- `403 REAUTHENTICATION_REQUIRED`: the sign-in is too old, and `current_password` is missing or
  wrong. A wrong one counts toward the login throttling (`429 RATE_LIMITED`).
- **Forgotten password:** sign in with an e-mail link, then call this within 10 minutes.

### `DELETE /api/v1/auth/password` (signed in)
Removes the password, so only the e-mail link signs in. Request `{"current_password": "..."}` with
the same rule as above.
- Response `204`. The other sessions are revoked and the "your password was changed" e-mail goes
  out.
- Also `204` when no password is set.
- `403 REAUTHENTICATION_REQUIRED` as above.

### `POST /api/v1/auth/refresh`
Needs the refresh cookie and `X-Requested-With`. Response `200 SessionResponse`, with the rotated
cookie.
- **Grace window:** within 60 seconds after a rotation, the token just replaced gets a new access
  token for the same session and no `Set-Cookie`, so tabs that refresh at the same time stay
  signed in.
- `401 SESSION_EXPIRED`, and the cookie is cleared:
  - no cookie, an unknown or expired one, or a revoked session;
  - a rotated token used again after the grace window, which counts as theft and revokes the
    session.
- The client calls it on page load, before an access token runs out, and after a
  `401 INVALID_TOKEN`.

### `POST /api/v1/auth/logout`
Needs the cookie and `X-Requested-With`; no access token. It revokes the session and clears the
cookie. Always `204`, also without a valid cookie.

### `GET /api/v1/auth/sessions` (signed in)
The user's active sessions, newest first:
```json
[ { "session_id": "uuid", "device_kind": "DESKTOP_BROWSER", "auth_method": "MAGIC_LINK", "authenticated_at": "...", "last_used_at": "...", "current": true } ]
```
- `device_kind`: `DESKTOP_BROWSER`, `MOBILE_BROWSER`, `UNKNOWN`, coarsely derived from the
  `User-Agent`. The frontend localizes it.
- `auth_method`: `MAGIC_LINK`, `PASSWORD`, `REGISTRATION`.
- `current` is `false` for every row when the token has no `sid`.

### `DELETE /api/v1/auth/sessions/{session_id}` and `DELETE /api/v1/auth/sessions` (signed in)
- **One session:** `204`. Someone else's, unknown or already ended: `404 SESSION_NOT_FOUND`.
- **Without an id:** all sessions of the user end, the current one included ("sign out
  everywhere"). `204`, the cookie is cleared, and `X-Requested-With` is needed.

### Invitations
An invitation is the only way into the system, and the only way an account gets another apartment
or a staff role. Who invites whom:

| Inviter | `kind` | Body | Effect when accepted |
| :--- | :--- | :--- | :--- |
| Chairman | `OWNER` | `{"kind": "OWNER", "email", "apartment_number": "14"}` | an `OWNER` membership in that apartment |
| Chairman | `STAFF` | `{"kind": "STAFF", "email", "building_role": "BOARD"}` (`BOARD`, `ACCOUNTANT`, `CHAIRMAN`) | the building role, if the account has none yet |
| Owner of the apartment | `TENANT` | `{"kind": "TENANT", "email", "apartment_id": "uuid"}` | a `TENANT` membership (`can_vote = false`) |

Board members do not invite. Family members (`RESIDENT`) are not invited (owner decision).

**Invitation object (`InvitationView`):**
```json
{ "invitation_id": "uuid", "kind": "TENANT", "email": "new@example.com", "apartment_id": "uuid", "apartment_number": "14", "building_role": null, "status": "PENDING", "mine": true, "created_at": "...", "expires_at": "..." }
```
- **`status`:**
  - `PENDING`;
  - `ACCEPTED`;
  - `EXPIRED` (the 7 days passed);
  - `NO_LONGER_VALID`: a `TENANT` invitation whose apartment has no active owner any more, or is
    gone. `TENANT` invitations are sent on behalf of the apartment's owners, so a resend by a
    co-owner keeps them valid; `OWNER` and `STAFF` invitations are sent on behalf of the board and
    stay valid while the building has an active chairman. `NO_LONGER_VALID` rows do not count
    toward the tenant limit.
- **`email`** is shown in full only to the person who typed it in, and masked to everyone else who
  may see the invitation (co-owners, the chairman, another chairman): `n***@example.com`.

#### `POST /api/v1/auth/invitations` (signed in)
Response `201 InvitationView`.
- The answer is the same whether the e-mail has an account or not.
- The invitation e-mail holds a link valid 7 days.
- A new person registers through it. A person with an account signs in and accepts, so nobody is
  added to an apartment or given a role without their consent.

Errors:
- `403 FORBIDDEN`: not the chairman, or (for `TENANT`) not an owner of that apartment.
- `404 APARTMENT_NOT_FOUND`: no apartment with that number or id in the building.
- `409 TENANT_LIMIT_REACHED`: the apartment already has `MAX_TENANTS_PER_APARTMENT` (default 3)
  active tenants and pending tenant invitations together.
- `409 INVITATION_ALREADY_PENDING`: an invitation of this kind for this e-mail and target is
  already pending (from anyone); resend it instead. An expired one is replaced by the new one.
- `422 VALIDATION_ERROR`.
- `429 RATE_LIMITED`: 10 invitations and resends per inviter per hour.

**Demo mode:** the invitation is stored, but its e-mail goes out only with `AUTH_MAIL_IN_DEMO=true`.

#### `GET /api/v1/auth/invitations` (signed in)
`InvitationView` items, newest first.
- The chairman sees all of the building.
- An owner sees the `TENANT` invitations of the apartments they own.

#### `POST /api/v1/auth/invitations/{invitation_id}/resend` and `DELETE /api/v1/auth/invitations/{invitation_id}`
- **Who:** the chairman; for a `TENANT` invitation, also any owner of the apartment.
- **Resend:** a `PENDING` or `EXPIRED` invitation gets a new link, the old one stops working, and
  the 7 days start again. Response `200 InvitationView`; the limit of `POST` applies.
- **Delete:** a `PENDING`, `EXPIRED` or `NO_LONGER_VALID` invitation, with its links: `204`.
- **Errors:**
  - an accepted invitation: `409 INVITATION_ACCEPTED`; to take access away, end the membership;
  - unknown, or not visible to the caller: `404 INVITATION_NOT_FOUND`.

#### `POST /api/v1/auth/invitations/accept` (signed in)
An existing account accepts an invitation after `SIGN_IN_TO_ACCEPT` and an explicit "Accept"
click. Request `{"token": "..."}` (the invitation link's token).
- **Who:** the e-mail of the invitation must be the signed-in account's e-mail; otherwise
  `400 INVALID_LINK`, with no hint why.
- **What happens:** in one transaction the invitation is applied, marked accepted and its link
  used up. Response `200 {"applied": true}`, or `{"applied": false}` when there was nothing to
  change.
- **It never lowers anything:**
  - an account that already has an active membership in that apartment keeps it unchanged (an
    `OWNER` invited as `TENANT` stays `OWNER`);
  - a `STAFF` invitation applies only to an account whose role is `USER`, except that a
    `CHAIRMAN` invitation raises any other role (so the recovery command of "First chairman" works
    for a board member); otherwise the role stays as it is (the chairman uses `PUT …/role`);
  - an ended membership in the same apartment is reopened, not duplicated.
- An invitation of another building, or one that no longer applies: `400 INVALID_LINK`.

### Residents, roles and closing accounts
#### `GET /api/v1/auth/apartments/{apartment_id}/residents` (owner of the apartment, chairman)
For the "My residents" page:
```json
{
  "residents": [ { "user_id": "uuid", "membership_id": "uuid", "relation_type": "TENANT", "display_label": "M. T. (Apt. 2)", "since": "...", "invited_email": "mari@example.com" } ],
  "pending_invitations": [ InvitationView ]
}
```
- `display_label` follows the person's own privacy mode for that apartment (section 0).
- `invited_email` is the e-mail the caller typed when inviting that person, if that invitation
  created or reopened this membership; otherwise `null`. Such an invitation is kept while its
  membership is active.
- No other e-mails and no phone numbers.
- Not an owner of the apartment and not the chairman: `403 FORBIDDEN`.

#### `DELETE /api/v1/auth/apartments/{apartment_id}/tenants/{user_id}` (owner of the apartment)
Ends that person's `TENANT` membership in the apartment (`ended_at`). `204`.
- Not a tenant there: `404 MEMBERSHIP_NOT_FOUND`.
- Not an owner of the apartment: `403 FORBIDDEN`.
- If it was the person's last active membership and they have no staff role, the account is
  closed (below).

#### `POST /api/v1/auth/apartments/{apartment_id}/tenants/{user_id}/replace` (owner of the apartment)
Request `{"email": "new.tenant@example.com"}`. In one transaction it ends that tenant's membership
as above and creates a `TENANT` invitation for the new e-mail. Response `201 InvitationView`.
Errors as in the two endpoints above; the tenant limit counts without the replaced tenant.

#### `DELETE /api/v1/auth/users/{user_id}/memberships/{membership_id}` (chairman)
Ends one membership of a person of the building, e.g. an owner who sold one of two flats.
- `204`. The same automatic closing applies.
- `404 MEMBERSHIP_NOT_FOUND`: not that person's active membership.
- `404 USER_NOT_FOUND`: unknown, closed or another building.

#### `PUT /api/v1/auth/users/{user_id}/role` (chairman)
Request `{"building_role": "BOARD"}` (`USER`, `BOARD`, `ACCOUNTANT`, `CHAIRMAN`). Response `200
{"user_id", "building_role", "account_closed": false}`.
- `409 LAST_CHAIRMAN`: it would leave the building without an active chairman. A handover is
  "give the other person `CHAIRMAN`, then lower your own role".
- Setting `USER` for a person without an active membership closes the account
  (`"account_closed": true`). The frontend asks for a confirmation first.
- `404 USER_NOT_FOUND`: unknown, closed or another building.

#### `DELETE /api/v1/auth/users/{user_id}` (chairman)
Closes an account. `204`; `404 USER_NOT_FOUND`; `409 LAST_CHAIRMAN`.

**Closing:**
- deactivates the account and ends all memberships;
- revokes all sessions and deletes the person's links, the invitations to their e-mail and their
  identities (passwords, later eID);
- removes the personal data: e-mail and phone become `null`, the name becomes "Former resident";
- notifications deletes the person's chat and device bindings, preferences and feed.

What stays:
- readings keep their apartment;
- forum posts keep a masked badge (section 0: a former member shows as the apartment only);
- the e-mail can be invited again later.

There is no endpoint to change an e-mail address; changing one's own e-mail comes later (E7 page,
scope).

### First chairman of a new installation
An operator command, not an endpoint: `uv run python -m src.modules.auth create-chairman --email
chair@example.com`.
- It creates a `STAFF` invitation with `CHAIRMAN` on behalf of the system, sends it, and prints
  the invitation id.
- **When it refuses:** the building already has an active chairman, unless `--force` is given
  (recovery when the only chairman lost their mailbox: the new chairman then lowers or closes the
  old account).
- **No mail:** when mail cannot go out (no e-mail plugin, or demo mode without
  `AUTH_MAIL_IN_DEMO`), it warns. With `--print-link`, it prints the invitation link to the
  operator's terminal instead, and to nowhere else.

### Error codes of this section
| Status | `error_code` |
| :--- | :--- |
| 400 | `INVALID_LINK` (new status) |
| 401 | `INVALID_CREDENTIALS`, `SESSION_EXPIRED`, `INVALID_TOKEN` |
| 403 | `CSRF_CHECK_FAILED`, `REAUTHENTICATION_REQUIRED`, `FORBIDDEN` |
| 404 | `SESSION_NOT_FOUND`, `INVITATION_NOT_FOUND`, `APARTMENT_NOT_FOUND`, `MEMBERSHIP_NOT_FOUND`, `USER_NOT_FOUND` |
| 409 | `EMAIL_ALREADY_REGISTERED` (registration only), `TENANT_LIMIT_REACHED`, `INVITATION_ALREADY_PENDING`, `INVITATION_ACCEPTED`, `LAST_CHAIRMAN` |
| 422 | `VALIDATION_ERROR`, `PASSWORD_TOO_WEAK` |
| 429 | `RATE_LIMITED` |
