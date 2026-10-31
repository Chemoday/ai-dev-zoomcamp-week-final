# Product Backlog (after the Phase 1 MVP)

Known problems and planned extensions that are deliberately **not** part of the MVP. Each entry
describes the problem, what the current model already supports, what blocks it, and a suggested
direction. Phase 1 tasks live in [TASKS.md](TASKS.md); phases in [ROADMAP_MVP.md](ROADMAP_MVP.md).

---

## B-1. Several buildings and management companies

- **Status:** open, design only. Target: Phase 3 (together with the managed cloud plane).
- **Raised:** 2026-10-25, during Task 5.

### Problem
The MVP assumes one association = one building = one instance ([VISION.md](VISION.md),
"Single-Tenant / Self-Hosted by Default"). Real associations do not always look like that. Two
different cases exist:

1. **One association, several buildings.** An Estonian *korteriühistu* is formed per immovable
   property, and one property can hold several buildings. Many HOAs / TSZh / OSBB also cover a
   small group of houses. This is still **one client** with several buildings inside.
2. **One management company, many associations.** Professional managers serve dozens of
   independent associations: property management firms and outsourced accountants in Estonia,
   *isännöitsijä* offices in Finland, *WEG-Verwalter* in Germany, management companies (UK) in
   Russia and Ukraine. One person works for **many separate clients**.

### What already fits
- Every domain table carries `building_id`, and composite foreign keys keep each row chain inside
  one building (`meters_*`, `comm_posts` / `comm_comments`, author apartments). Data of two
  buildings cannot be mixed even when they share a database.
- Apartment memberships are per apartment, so a person can already be a member of apartments in
  different buildings at the table level.

### What blocks it
- `core_users.building_id` is `NOT NULL` and single: a user belongs to exactly one building, and
  `CurrentUserDTO.building_id` is the scope of every query.
- `core_users.building_role` is one role per user, so "chairman of building A, plain owner in
  building B" or "accountant of five buildings" cannot be expressed.
- `PropertyService._memberships_of` returns only memberships of the user's own building, and the
  forum, alerts and meters all scope by that one building.
- There is no entity above the building: no association that owns buildings, holds the board and
  could later hold billing and governance (votes are per association, not per building).

### Suggested direction (additive, expand/contract migrations)
1. **Association level.** Add `property_associations`; `property_buildings.association_id`.
   For case 1, board roles, official notices, votes and billing belong to the association;
   meters, entrances and emergencies stay per building.
2. **Roles per scope.** Replace `core_users.building_role` with a table
   `property_role_assignments (user_id, association_id, building_id NULL, role)`. Keep the old
   column during the transition (expand), drop it later (contract).
3. **Active context per request.** The caller picks the building or association they act in
   (path segment or an `X-Building-Id` header, validated against their assignments and
   memberships). `CurrentUserDTO` is built for that context, so existing services keep scoping by
   one `building_id` and do not change.
4. **Case 2 stays a deployment question.** Keep one instance per association (privacy, backups,
   zero cross-client leaks). A management company gets either one account per instance or a
   federated portal on the managed cloud plane that lists its instances. Moving to a multi-tenant
   database is possible but contradicts the single-tenant principle and needs its own decision.

### Open questions
- Is case 1 frequent enough for the target users to do it before billing (Phase 2)?
- For case 2: separate logins per instance, or one identity (Smart-ID / Web eID) across instances?
- Do official notices and forum topics belong to the association or to each building?

---

## B-2. Forum moderation beyond the MVP

- **Status:** open. Target: Phase 2. **Tracked in:**
  [#18](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/18).
- **Raised:** 2026-10-25, during Task 5.

### Current state (MVP)
The chairman can soft-delete a forum topic (`DELETE /api/v1/communication/posts/{id}`,
[API.md](API.md), section 4). The topic and its comments disappear for everyone; the row keeps
`deleted_at` and `deleted_by_user_id`. Emergencies are resolved instead, and official notices
cannot be deleted.

### Missing
- Authors edit or delete their own posts and comments.
- The board hides or deletes single comments, not only whole topics.
- Residents report abusive posts or comments; the board works through a report queue.
- A moderation log: who hid or deleted what, when and why, without PII in logs or spans.
- Retention: soft-deleted content is purged after a fixed period (GDPR), keeping only the audit
  facts (who, when, which post id).
- Edit history, so an edited post cannot silently change what neighbours already answered to.

### Open questions
- Should `BOARD` members moderate too? [VISION.md](VISION.md) says the board moderates the
  forum; the MVP allows only the chairman.
- Should a deleted topic show a placeholder ("Removed by the board") instead of vanishing, so the
  conversation history stays understandable?
- How long is the retention period, and does it differ for reported content?

---

## B-3. Database backups and durable storage

- **Status:** open, not part of the MVP. Target: before the first real association uses the
  platform.
- **Raised:** 2026-10-25.

### Problem
There are **no backups at all**. [VISION.md](VISION.md) promises "trivial single-file database
backups", but nothing creates them, and a restore has never been tried.

Where the data lives today:
- PostgreSQL data is in a named Docker volume, not in the image or the container. Removing the
  image or the container, `docker compose down` and every redeploy keep the data.
- On the VPS each environment has its own volume (`aap-dev_pgdata`, `aap-prod_pgdata`), its own
  internal network and its own password, see [DEPLOYMENT.md](DEPLOYMENT.md).
- The data is lost by `docker compose down -v`, `docker volume rm` or
  `docker system prune --volumes`. The VPS is shared with unrelated services, so a routine
  clean-up of Docker on that host can wipe both environments, with no way back.
- Locally the volume is named after the checkout directory (`<dir>_pgdata`); `down -v` deletes it.

### Idea for a solution (to be discussed)
1. **Data in a host directory per environment:** bind-mount `/opt/aap/<env>/pgdata` instead of a
   named volume. The environment name in the path keeps dev and prod apart, `docker ... prune`
   never touches it, and host-level backups see it. The existing volumes are moved once (about a
   minute of database downtime per environment).
2. **Nightly `pg_dump -Fc`** per environment into `/opt/aap/<env>/backups`, keeping 7 days, run
   on the host (cron or a systemd timer) so it works even when the app container is broken.
3. **Restore procedure** in DEPLOYMENT.md, plus an automated restore check: load the newest dump
   into a scratch database and run a sanity query. A backup that was never restored does not count.
4. **Off-server copy** (later): the dumps also leave the VPS, e.g. to object storage, encrypted.
5. **Locally** keep the named volume and document that `docker compose down -v` deletes the data.

### Open questions
- How many days or versions to keep, and is a copy outside the VPS needed from the start?
- Should backups be a feature of the product (the chairman downloads one) or only operations?
- Point-in-time recovery (WAL archiving) or are nightly dumps enough for an association?

---

## B-4. A database must belong to exactly one environment

- **Status:** open, not part of the MVP. Related to B-3 (restores and moved data directories are
  exactly when this goes wrong).
- **Raised:** 2026-10-25.

### Problem
Nothing checks that the database the service connects to was created for this environment. If
by mistake the wrong data is attached, the service starts on it as if nothing happened. Examples:
- a prod backup is restored into the dev database (or the other way round);
- a data directory or volume is copied or mounted for the wrong environment;
- a wrong `DATABASE_URL` in an env file points one environment, or one association's instance,
  at another one's database.

The consequences are silent: dev experiments change real residents' readings, demo-mode users
(`X-Demo-User-Id`) suddenly act on real data, or notifications go out to the wrong people.

What protects us today is only the topology: on the VPS dev and prod run in separate Compose
projects with separate networks and passwords, so a dev backend cannot reach the prod database.
There is no check inside the data itself.

### Idea for a solution (to be discussed)
1. On the first start the database records which environment and instance it was created for,
   e.g. a one-row table `core_instance (instance_id, environment, created_at)`.
2. On every start the service compares it with its own configuration (`ENVIRONMENT` plus an
   `INSTANCE_ID` from the server's env file). On a mismatch it refuses to start with a clear log
   line; `/health/ready` fails, so `deploy.sh` rolls back automatically.
3. Deliberately moving data (e.g. copying prod into dev for debugging) is an explicit command that
   rewrites the marker, ideally after anonymising the copy.

### Open questions
- Is `ENVIRONMENT` enough, or does each instance need its own id (several associations on one
  host, see B-1)?
- Should a prod copy in dev be anonymised automatically?

## B-5. Readings more often than once a period (smart meters)

- **Status:** note only, no work planned. Raised 2026-10-31.

### Current state
Several submissions within one period are already handled: `UNIQUE(meter_id, period_id)` keeps
one reading per meter and period, and every change writes an audit row to
`meters_reading_logs`. A new submission overwrites the value within the rules of
`POST /api/v1/meters/readings` in docs/API.md (residents only while the period is open, never over
a board correction, never once a later period has a reading).
Nothing to change for manual readings.

### When it matters
Smart electricity meters deliver hourly or 15-minute values, used to split costs by the exchange
price. If we support that, store the raw time series in a separate table (fed by the IoT readings
API, epic E4) and derive the period reading from it; do not put time series into
`meters_readings`.

## B-6. Debt reminders

- **Status:** open, depends on epic E2. Raised 2026-10-31.

### Problem
Reminding residents about unpaid bills needs payment data. By the decision in
[E2](epics/E2_BILLING_EXPORT.md) payments and balances live in the external accounting software,
so we do not have them.

### Options
- Import bank statements (ISO 20022 camt.053) and match payments by reference number.
- Read balances from the accounting software API (country plugin, e.g. Merit Aktiva).

Delivery itself reuses epic E1 (a new event type, e.g. `PaymentOverdue`).
