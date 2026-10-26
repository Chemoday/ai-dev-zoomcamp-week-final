# Frontend Integration Guide (Task 6)

For the agent that builds the client in `frontend/`. The **contract** is
[`openapi.json`](openapi.json) (generated from the backend, always current) and the **rules** are in
[API.md](API.md). This guide lists what is easy to get wrong from the client side: behaviour that
the schema alone does not show, and places where the backend deliberately differs from what a
client would assume.

Do not change backend code from the frontend task. If the API is missing something, write it down
as a request for the backend (an issue) instead.

---

## 1. Project setup

- **Types from the contract.** `npx openapi-typescript docs/openapi.json -o frontend/src/api/schema.ts`.
  Regenerate whenever `docs/openapi.json` changes; never edit the generated file.
- **Same origin, no CORS.** The backend does not enable CORS. In development, proxy `/api`,
  `/health` and `/docs` to the backend (e.g. the Vite `server.proxy` option) instead of calling
  `http://localhost:8000` directly. Locally the backend listens on `BACKEND_PORT` (default `8000`).
- **Single command (course requirement).** `docker compose up --build` must still start the whole
  application, including the built frontend, and `pytest` must stay green. Plan how the built files
  are served (the backend with FastAPI `StaticFiles`, or Caddy on the VPS) as part of Task 6; the
  backend serves nothing at `/` yet.
- **Routes the frontend must not shadow:** `/api/v1/...`, `/health`, `/health/ready`, `/docs`,
  `/redoc`, `/openapi.json`. An SPA fallback (`index.html` for unknown paths) must exclude them, or API 404s
  turn into HTML pages.
- **Frontend tests run with one command** and cover the two-popup flow (Task 6 acceptance
  criteria). Mock the API with the generated types, so a contract change breaks the tests.
- **Mobile first:** the layout must work at 375 px.
- **Language:** everything in the repository (code, comments, docs, commit messages) is English.
  Ask the project owner which language the UI texts use before writing them.

## 2. Acting as a user (demo mode)

There is **no login endpoint** in the MVP. The UI acts as a seeded user:

1. `GET /api/v1/demo/users` (no auth) returns the role switcher entries:
   `user_id`, `label` ("Owner Apt 14"), `building_role`, `apartment_number`, `relation_type`.
2. Send the chosen id as **`X-Demo-User-Id: <user_id>`** on every `/api/v1` request.
3. Keep the choice client-side (e.g. `localStorage`); default to "Owner Apt 14", where the demo
   story starts.

Rules:
- Read the ids from the endpoint; do not hard-code them.
- `GET /api/v1/demo/users` answers `404` when demo mode is off, and `[]` on an instance without demo
  data. Show a clear message instead of an empty switcher.
- If a request ever also carries `Authorization: Bearer ...`, the token wins over the demo header.
- `401` (`NOT_AUTHENTICATED`, `UNKNOWN_USER`) means the stored id is no longer valid: clear it and
  show the switcher again.

**The public demo is shared.** Every grader acts on the same database. Someone may already have
submitted Apt 14's October reading, acknowledged or resolved the emergency, moved the period to
`REVIEW_BY_BOARD`, or deleted a forum topic. The UI must handle every state the API can return,
not only the freshly seeded one ([API.md, section 5](API.md) describes the initial state). There
is no "reset demo" endpoint.

## 3. What each role sees

Build the navigation from `GET /api/v1/property/me` (`building_role`, `memberships[]` with
`can_submit_readings`), not from the switcher label.

| View | `USER` (resident) | `BOARD` | `CHAIRMAN` | `ACCOUNTANT` |
| :--- | :--- | :--- | :--- | :--- |
| My meters (`/meters/my-dashboard`) | own apartments | own apartments (Apt 15) | none (no apartment) | none |
| Submit a reading | own meters, if `can_submit_readings` | own apartment only; matrix rows are read-only (`403`) | any meter, incl. master meters | any meter, incl. master meters |
| Matrix, history, CSV export | history of own readings | yes | yes | yes |
| Period status, estimate missing | no | no | yes | yes |
| Publish emergency / notice | no | yes | yes | no |
| Resolve an emergency | no | yes | yes | no |
| Delete a forum topic | no | no | yes | no |
| Forum: read, post topics, comment | yes | yes | yes | yes |

A user with no apartment gets `apartments: []` from the dashboard; hide the meter card, do not
show an error. `GET /property/me` never returns the user's own name or email: greet with the
switcher label or the membership's `preview_badge`.

## 4. Errors

Every error has the same JSON body (`ErrorResponse` in the contract):
`{"error_code": "...", "message": "...", ...extra}`.

- **Branch and localize on `error_code`, never on `message`.** Messages are English developer
  texts and may change. Each endpoint in `openapi.json` lists the `error_code` values per status.
- **`422 VALIDATION_ERROR`** carries `errors: [{"loc": ["body", "value"], "message", "type"}]`.
  Map `loc[1]` to the form field. The submitted values are not echoed back. Other `422` codes
  (`READING_BELOW_PREVIOUS`, `INVALID_TARGET`, ...) have no `errors` list but extra fields.
- **`404` also means "not visible to you".** A post that does not target the caller, or was deleted
  by the chairman, is `404 POST_NOT_FOUND`, the same as an unknown id. Remove such items from lists
  instead of showing a broken page.
- **`500 INTERNAL_SERVER_ERROR`** carries `trace_id`. Show it in the error message ("Error code:
  …"), so a report can be matched to the backend logs. Every response also has an `X-Trace-Id`
  header; log it with failed requests.

## 5. Meter values are decimal strings

- Values and deltas are **strings in both directions** (`"1254.100"`). A JSON number is rejected
  with `422`. Input pattern: `^[0-9]{1,9}(\.[0-9]{1,3})?$`; responses always have exactly
  3 decimals (`"1254.000"`).
- **Never convert to a JavaScript `number` for arithmetic** (`0.1 + 0.2`), and never round. To
  compare or subtract (e.g. "consumption so far"), use a decimal library or scaled `BigInt`
  (multiply by 1000).
- **Normalize user input before sending:** many locales type a comma (`"128,5"`). Replace it with a
  dot, strip spaces, and reject thousands separators. Sending `"128.5"` is fine; the backend
  returns `"128.500"`.
- Negative values exist in responses: `unallocated_loss` can be below zero (it points to a wrong
  reading). `master_total` and `unallocated_loss` are `null` while a master meter has no reading.
- Show the unit from the payload (`m3`, `kWh`, `MWh`); kWh and MWh are never summed together.

## 6. The two-popup confirmation flow

`POST /api/v1/meters/readings` with `{meter_id, period_code, value, confirmation_level, reason}`.

```text
level 0 ──► 200 saved (normal value)                          done
        └─► 409 ANOMALY_CONFIRMATION_STEP_1  → popup #1 "unusually high, are you sure?"
               confirm ──► same value, level 1
                           └─► 409 ANOMALY_CONFIRMATION_STEP_2 → popup #2 "double-check, goes to billing"
                                  confirm ──► same value, level 2 ──► 200, is_anomaly_confirmed = true
any level ──► 422 READING_BELOW_PREVIOUS  → show previous_value, no popup
```

- **Always start at `confirmation_level: 0`.** The backend accepts level 2 directly, so a client
  that skips the popups would silently confirm anomalies. The flow is the UI's job.
- **If the user edits the value between the popups, start again at level 0.** Cancel at any popup
  sends nothing.
- STEP_1 carries `consumption_delta`, `average_delta` and `max_reasonable_delta` for the popup text;
  STEP_2 carries `consumption_delta`. Show the delta with the unit.
- Disable the submit button while a request is in flight; a double click with the same value is
  a harmless no-op on the backend, but a second popup chain is confusing.
- Other outcomes to handle on this form:
  - `409 PERIOD_CLOSED` (residents after the period moved to review) and `409 PERIOD_LOCKED` (board,
    locked period): show "submission closed", refresh the dashboard.
  - `409 READING_SET_BY_BOARD`: the board corrected this reading; the resident cannot change it.
  - `409 LATER_READING_EXISTS`: an older period cannot change any more.
  - `422 REASON_REQUIRED` (chairman / accountant changing an existing reading): show a required
    "reason" field whenever the cell already has a reading.
  - `403 FORBIDDEN`: a board member on someone else's meter, or a member without
    `can_submit_readings`. Better: do not offer the action.

## 7. Dashboard, matrix and periods

- `GET /meters/my-dashboard` without `period_code` picks the period residents can submit to.
  `period` is `null` when the building has none. `reading` is `null` until something was submitted
  for that meter; `previous_value` is what the new value must not go below.
- A user can belong to several apartments: render one card per apartment.
- **Consumption hints**: each dashboard meter has `average_delta` (the 3-period average the anomaly
  check uses) and `previous_delta` (last period's consumption), both decimal strings or `null`.
  Show them before submitting ("usually about 4.2 m³"); a delta far above `average_delta` will
  trigger the popups. There is no building average for residents: do not compute or show one.
- **Period selector and export archive**: `GET /meters/periods` lists every period of the building,
  newest first (`period_code`, `status`, `due_date`). Use it instead of guessing period codes.
- **Association header**: `GET /property/building` returns the name, registry code, address,
  apartment and entrance counts and `heated_area_m2` (a decimal string). Load it once per session.
- **Matrix** (`/meters/periods/{code}/matrix`): apartment status is the most urgent of its meters
  (`MISSING` > `ANOMALY_CONFIRMED` > `AUTO_ESTIMATED` > `SUBMITTED`). `master_meters` holds the
  basement meters with their ids, so the board can submit them from the matrix. The matrix has no
  names; residents are not identified in it.
- **Period status** (`PATCH /meters/periods/{code}/status`): only
  `OPEN_FOR_RESIDENTS ↔ REVIEW_BY_BOARD ↔ LOCKED`, one step at a time. A wrong step is
  `409 INVALID_PERIOD_TRANSITION` with `allowed_statuses`: use them to build the buttons.
- **Estimate missing** works only in `REVIEW_BY_BOARD` (`409 PERIOD_NOT_IN_REVIEW`); running it
  again estimates nothing new (`estimated_count: 0`). Refresh the matrix afterwards.
- **History** (`/meters/readings/{id}/history`): entries are chronological; `changed_by` is a masked
  badge or `null` for the system. Never try to show who it "really" was.
- **CSV export needs the auth header**, so a plain `<a href>` does not work: `fetch` it with
  `X-Demo-User-Id`, turn the response into a `Blob` and download it with the file name from
  `Content-Disposition` (`readings-2026-10.csv`). `404 EXPORTER_NOT_ENABLED` hides the button.
- `due_date` is informational; periods do not close automatically.

## 8. Emergencies and the forum

- **Banner:** `GET /communication/emergencies/active` returns the alerts that target the caller.
  Show the sticky red banner for items with `acknowledged_at: null`; "Acknowledge" is
  `POST /emergencies/{id}/acknowledge` (idempotent, per user). Poll the endpoint (e.g. every
  30-60 s and on window focus); there are no push notifications in the MVP.
- The chairman and the board see **every** active emergency, including those targeted at another
  entrance; show the target ("Entrance 2") in their banner.
- **Resolve** (`POST /emergencies/{id}/resolve`, board) ends it for everyone; it stays readable in
  the feed with `is_active: false`.
- **Publishing:** `target_scope` `ALL_BUILDING` (no other target fields), `ENTRANCE`
  (`target_entrance`) or `APARTMENT_LIST` (`target_apartment_ids`, 1..500). Forum topics are
  always `ALL_BUILDING`. Apartment ids and entrances come from the chairman matrix
  (`apartments[].apartment_id`, `entrance`, `floor`); there is no separate apartments endpoint.
- **Author badges** are already masked by the backend. Render
  `"<display_label> • <role_badge>"` (`"Apt. 14 • OWNER"`). `display_label` is English text that
  may contain a name the author chose to show; display it as is, do not parse or translate it.
  `role_badge` (`OWNER`, `RESIDENT`, `BOARD`, `ACCOUNTANT`) can be localized.
- **Privacy mode selector:** `PATCH /property/memberships/{membership_id}/privacy` per apartment;
  the response's `preview_badge` is exactly what neighbours will see. Badges are resolved on every
  read, so old posts change too after the switch: reload the feed.
- **Writing on behalf of an apartment:** a user with several apartments may pass
  `author_apartment_id` (one of their own). Without it, forum topics and comments use the first
  apartment; board posts use none.
- **`is_mine`** marks the caller's own posts and comments; there is no other author identity.
- **Feed paging:** `GET /communication/posts?limit=&offset=`, newest first. New posts shift the
  offsets, so de-duplicate by `id` when loading more. Post detail pages its comments with
  `comments_limit` / `comments_offset`; `comments_count` is always the total.
- `comments_enabled: false` hides the comment form (`409 COMMENTS_DISABLED` otherwise).
- **Delete** (`DELETE /communication/posts/{id}`, chairman, forum topics only) answers `204`;
  emergencies and notices answer `409 POST_NOT_DELETABLE`.
- **Free text limits:** title 1..255, post body 1..10000, comment 1..4000 characters after
  trimming. Control characters are rejected. Show a counter and trim before checking "empty".

## 9. Dates and ids

- Timestamps are ISO 8601 in UTC (`2026-11-04T23:59:59Z`). Display them in the building's local
  time (Tallinn, `Europe/Tallinn`) with `Intl.DateTimeFormat`, not with string slicing.
- All ids are UUID strings; treat them as opaque.

## 10. Privacy checklist for the UI

- Never display, store or log anything that looks like a real name except a `display_label` the
  backend returned.
- Do not put user ids, badges or free text into analytics or URLs beyond what routing needs (post
  ids are fine).
- Do not cache other users' data across a role switch: clear the client cache when the acting
  user changes.
