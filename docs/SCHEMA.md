# Database Schema Specification (PostgreSQL 16 + SQLAlchemy 2.0)

All tables use `UUID` primary keys (`uuid4`), UTC timestamps (`TIMESTAMPTZ`), and module-prefixed table names to maintain strict logical boundaries inside a single PostgreSQL database.

## 1. Users & Identity Providers (tables `core_*`, owned by module `property`)

> **Ownership rule:** the `core_users` and `core_user_identities` tables keep the `core_` prefix because every module references users by FK, but their SQLAlchemy models (`User`, `UserIdentity`) live in `src/modules/property/models.py`, as listed in `src/core/models_registry.py`. `src/core/` holds no domain models. Other modules never import these models: they read users only through DTOs: `CurrentUserDTO` from `get_current_user` (`src/core/security.py`) or `PropertyService` methods such as `get_masked_author_badge`.

### Table: `core_users`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Internal user ID |
| `building_id` | `UUID` | `FK(property_buildings.id), NOT NULL, INDEX` | Building the user belongs to. `building_role` applies to this building, and it is the source of `CurrentUserDTO.building_id` (also for staff without an apartment membership) |
| `email` | `VARCHAR(255)` | `UNIQUE, NULLABLE` | Email (optional for offline/PIN-only elderly residents) |
| `full_name` | `VARCHAR(255)` | `NOT NULL` | Real name (NEVER exposed raw in forum APIs or telemetry logs) |
| `phone` | `VARCHAR(50)` | `NULLABLE` | Emergency contact phone (visible only to Chairman/Board) |
| `building_role` | `VARCHAR(32)` | `NOT NULL, DEFAULT 'USER'` | Enum: `CHAIRMAN`, `BOARD`, `ACCOUNTANT`, `USER` |
| `is_active` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | Account status |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Creation timestamp |

### Table: `core_user_identities`
*(Decouples authentication from user profile; ready for Estonian Smart-ID / Web eID / Magic Links without storing raw `isikukood` in plain text)*
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Identity record ID |
| `user_id` | `UUID` | `FK(core_users.id), INDEX` | Linked user |
| `provider` | `VARCHAR(50)` | `NOT NULL` | `'password'`, `'pin_token'`, `'ee_smart_id'`, `'ee_id_card'` |
| `subject_hash` | `VARCHAR(128)` | `NOT NULL, INDEX` | Salted SHA-256 hash of identifier (e.g., `hash("EE:3850101xxxx")`) |
| `secret_data` | `VARCHAR(255)` | `NULLABLE` | Bcrypt password hash or token hash |
| `UNIQUE` | `(provider, subject_hash)` | | Prevents duplicate external identities |

---

## 2. Module: `property` (Building, Apartments & Privacy-Aware Memberships)

### Table: `property_buildings`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Building ID |
| `name` | `VARCHAR(255)` | `NOT NULL` | e.g., `"KÜ Sõpruse pst 12"` |
| `address` | `VARCHAR(500)` | `NOT NULL` | Physical address |
| `country_code` | `VARCHAR(2)` | `NOT NULL, DEFAULT 'EE'` | ISO country code for active country plugins |
| `reading_deadline_day` | `INTEGER` | `NOT NULL, DEFAULT 4` | Day of next month until readings are open (e.g., `4`) |

### Table: `property_apartments`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Apartment ID |
| `building_id` | `UUID` | `FK(property_buildings.id)` | Parent building |
| `number` | `VARCHAR(32)` | `NOT NULL` | Apartment number (e.g., `"14"`, `"14A"`) |
| `entrance` | `INTEGER` | `NOT NULL, DEFAULT 1` | Entrance / Stairwell number (for targeted emergency alerts) |
| `floor` | `INTEGER` | `NOT NULL, DEFAULT 1` | Floor number |
| `riser_group` | `VARCHAR(50)` | `NULLABLE` | Plumbing riser identifier |
| `area_m2` | `NUMERIC(8, 2)` | `NOT NULL` | Heated area in m² (prepared for Phase 2 Billing) |
| `UNIQUE` | `(building_id, number)` | | Unique apartment number per building |

### Table: `property_memberships`
*(Models hierarchy inside an apartment: Owner vs. Tenant/Resident + Privacy Display Settings)*
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Membership ID |
| `apartment_id` | `UUID` | `FK(property_apartments.id), INDEX` | Target apartment |
| `user_id` | `UUID` | `FK(core_users.id), INDEX` | Linked user |
| `relation_type` | `VARCHAR(32)` | `NOT NULL` | Enum: `OWNER` (Apartment Admin), `TENANT`, `RESIDENT` |
| `can_submit_readings`| `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | Allowed to submit water/utility readings |
| `can_view_bills` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | Allowed to see apartment invoices |
| `can_vote` | `BOOLEAN` | `NOT NULL, DEFAULT FALSE` | `TRUE` only for `OWNER` |
| `privacy_display_mode`| `VARCHAR(32)`| `NOT NULL, DEFAULT 'APARTMENT_ONLY'` | Enum: `APARTMENT_ONLY`, `INITIALS`, `FULL_NAME` |
| `UNIQUE` | `(apartment_id, user_id)` | | One membership record per user per apartment |

---

## 3. Module: `meters` (Devices, Periods, Readings & Audit History)

### Table: `meters_devices`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Meter ID |
| `building_id` | `UUID` | `FK(property_buildings.id), INDEX` | Always populated |
| `apartment_id` | `UUID` | `FK(property_apartments.id), NULLABLE, INDEX` | **`NULL` = Building Master Meter in basement**; `UUID` = Apartment meter |
| `resource_type` | `VARCHAR(32)` | `NOT NULL` | Enum: `COLD_WATER`, `HOT_WATER`, `ELECTRICITY`, `HEAT`, `GAS` |
| `serial_number` | `VARCHAR(100)` | `NOT NULL` | Physical meter serial number |
| `location_label` | `VARCHAR(100)` | `NOT NULL, DEFAULT 'Main'` | e.g., `'Kitchen'`, `'Bathroom'`, `'Basement Master'` |
| `unit` | `VARCHAR(16)` | `NOT NULL, DEFAULT 'm3'` | `'m3'`, `'kWh'`, `'MWh'` |
| `initial_value` | `NUMERIC(12, 3)`| `NOT NULL, DEFAULT 0.000` | Starting reading upon installation/replacement |
| `max_monthly_delta` | `NUMERIC(12, 3)`| `NOT NULL, DEFAULT 25.000` | Threshold triggering the 2-step anomaly confirmation |
| `is_active` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | Set to `FALSE` when meter is replaced |

### Table: `meters_periods`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Period ID |
| `building_id` | `UUID` | `FK(property_buildings.id)` | Building ID |
| `period_code` | `VARCHAR(7)` | `NOT NULL, INDEX` | Format `'YYYY-MM'` (e.g., `'2026-10'`) |
| `status` | `VARCHAR(32)` | `NOT NULL` | Enum: `OPEN_FOR_RESIDENTS`, `REVIEW_BY_BOARD`, `LOCKED` |
| `due_date` | `TIMESTAMPTZ` | `NOT NULL` | e.g., `2026-11-04 23:59:59 UTC` |
| `UNIQUE` | `(building_id, period_code)` | | One accounting period per month per building |

### Table: `meters_readings`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Reading ID |
| `building_id` | `UUID` | `FK(property_buildings.id), NOT NULL` | Building of both the meter and the period (enforced, see below) |
| `meter_id` | `UUID` | `FK(meters_devices.id), INDEX` | Target meter |
| `period_id` | `UUID` | `FK(meters_periods.id), INDEX` | Accounting period |
| `value` | `NUMERIC(12, 3)`| `NOT NULL` | Current cumulative meter reading |
| `previous_value` | `NUMERIC(12, 3)`| `NOT NULL` | Snapshot of prior period's value (or `initial_value`) |
| `consumption_delta` | `NUMERIC(12, 3)`| `NOT NULL` | `value - previous_value` |
| `source` | `VARCHAR(32)` | `NOT NULL` | Enum: `RESIDENT`, `ADMIN_OVERRIDE`, `AUTO_ESTIMATED`, `IOT_WEBHOOK` |
| `is_anomaly_confirmed`| `BOOLEAN` | `NOT NULL, DEFAULT FALSE` | `TRUE` if user passed 2-step popup confirmation |
| `submitted_by_user_id`| `UUID` | `FK(core_users.id), NULLABLE`| User who submitted/modified (NULL if system `AUTO_ESTIMATED`) |
| `recorded_at` | `TIMESTAMPTZ` | `NOT NULL` | Timestamp of last modification (ORM `onupdate`; a raw `INSERT … ON CONFLICT DO UPDATE` must set it explicitly) |
| `UNIQUE` | `(meter_id, period_id)` | | **Strictly 1 active reading per meter per period** |

**Integrity constraints of `meters_*` tables** (enforced by PostgreSQL, so no code path can bypass them):
- Every enum column (`resource_type`, `unit`, `status`, `source`) has a named `CHECK (… IN (…))`.
- `meters_periods.period_code` must match `^[0-9]{4}-(0[1-9]|1[0-2])$`.
- `meters_devices`: `initial_value >= 0` and `max_monthly_delta > 0`.
- `meters_readings`: `value >= 0`, `previous_value >= 0`, and `consumption_delta = value - previous_value` (the stored delta can never disagree with the values).
- `meters_reading_logs` is append-only: triggers reject every `UPDATE`, `DELETE` and `TRUNCATE`.
- **One building per row chain.** `meters_devices` and `meters_periods` are `UNIQUE (id, building_id)`, and `property_apartments` is `UNIQUE (id, building_id)`. Composite foreign keys then guarantee that:
  - a reading's meter and period belong to the reading's building: `(meter_id, building_id)` → `meters_devices` and `(period_id, building_id)` → `meters_periods`;
  - an apartment meter's apartment is in the meter's building: `(apartment_id, building_id)` → `property_apartments`. Master meters with `apartment_id NULL` are not checked.
- `CHECK` constraints and triggers are invisible to `alembic check`. `tests/core/test_migrations.py` therefore compares every constraint and index of a migrated database with one built from the models, and asserts that the triggers exist.
- **Known gap (additive migration later):** there is no link from a replaced meter to its successor, and no `installed_at` / `deactivated_at`. Only `is_active` exists.

### Table: `meters_reading_logs` (Immutable Audit Trail)
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Log entry ID |
| `reading_id` | `UUID` | `FK(meters_readings.id), INDEX`| Parent reading record |
| `old_value` | `NUMERIC(12, 3)`| `NULLABLE` | `NULL` on initial insert; previous value on update |
| `new_value` | `NUMERIC(12, 3)`| `NOT NULL` | New value saved |
| `source` | `VARCHAR(32)` | `NOT NULL` | Source of this specific change |
| `changed_by_user_id` | `UUID` | `FK(core_users.id), NULLABLE`| Actor ID |
| `reason` | `VARCHAR(500)` | `NULLABLE` | e.g., `"Resident update"`, `"Chairman correction"`, `"3-month avg estimate"` |
| `changed_at` | `TIMESTAMPTZ` | `NOT NULL` | When the change occurred |

---

## 4. Module: `communication` (Emergency Alerts, Notices & Micro-Forum)

### Table: `comm_posts`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Post ID |
| `building_id` | `UUID` | `FK(property_buildings.id), INDEX` | Building ID |
| `author_user_id` | `UUID` | `FK(core_users.id), NOT NULL, INDEX` | Internal author ID (masked before API output, never returned) |
| `author_apartment_id`| `UUID` | `NULLABLE` | Context apartment for privacy badge resolution (`NULL` = no apartment context, e.g. a board post) |
| `post_type` | `VARCHAR(32)` | `NOT NULL, INDEX` | Enum: `EMERGENCY`, `OFFICIAL_NOTICE`, `FORUM_TOPIC` |
| `category` | `VARCHAR(50)` | `NOT NULL, DEFAULT 'GENERAL'` | Enum: `GENERAL`, `WATER_SHUTOFF`, `MAINTENANCE`, `MARKETPLACE` |
| `title` | `VARCHAR(255)` | `NOT NULL` | Headline |
| `body` | `TEXT` | `NOT NULL` | Content (the API caps it at 10000 characters) |
| `target_scope` | `VARCHAR(32)` | `NOT NULL, DEFAULT 'ALL_BUILDING'`| Enum: `ALL_BUILDING`, `ENTRANCE`, `APARTMENT_LIST` |
| `target_entrance` | `INTEGER` | `NULLABLE` | Populated if `target_scope == 'ENTRANCE'` |
| `target_apartment_ids`| `JSONB` | `NULLABLE` | Non-empty array of apartment UUID strings if `target_scope == 'APARTMENT_LIST'` (SQL `NULL` otherwise, never JSON `null`) |
| `is_active` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | For `EMERGENCY`: `TRUE` while the banner is active, `FALSE` once resolved. Always `TRUE` for other types |
| `comments_enabled` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | Board can disable comments on official notices |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL, INDEX` | Creation timestamp |
| `deleted_at` | `TIMESTAMPTZ` | `NULLABLE` | Set when the chairman deleted the forum topic (soft delete: hidden from every read) |
| `deleted_by_user_id` | `UUID` | `FK(core_users.id), NULLABLE` | Who deleted it |

### Table: `comm_comments`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Comment ID |
| `post_id` | `UUID` | `NOT NULL, INDEX` | Parent post (composite FK with `building_id`, see below) |
| `building_id` | `UUID` | `FK(property_buildings.id), NOT NULL` | The post's building; ties the comment and its author apartment to it |
| `author_user_id` | `UUID` | `FK(core_users.id), NOT NULL, INDEX` | Internal user ID (never returned) |
| `author_apartment_id`| `UUID` | `NULLABLE` | Context apartment |
| `body` | `TEXT` | `NOT NULL` | Comment text (the API caps it at 4000 characters) |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Timestamp |

### Table: `comm_emergency_acks`
*(Who pressed "Acknowledge" on an emergency banner; the banner stays hidden for that user)*
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Acknowledgement ID |
| `post_id` | `UUID` | `FK(comm_posts.id), NOT NULL` | The emergency post |
| `user_id` | `UUID` | `FK(core_users.id), NOT NULL, INDEX` | The user who acknowledged |
| `acknowledged_at` | `TIMESTAMPTZ` | `NOT NULL` | First acknowledgement (repeats keep it) |
| `UNIQUE` | `(post_id, user_id)` | | One acknowledgement per user and post |

**Integrity constraints of `comm_*` tables** (enforced by PostgreSQL, covered by `tests/core/test_migrations.py`):
- Every enum column (`post_type`, `category`, `target_scope`) has a named `CHECK (… IN (…))`.
- `ck_comm_posts_target_consistent`: `ALL_BUILDING` has neither entrance nor apartment list; `ENTRANCE` has an entrance and no list; `APARTMENT_LIST` has a non-empty JSON array of canonical lowercase UUID strings (checked with `jsonb_path_exists`) and no entrance. The list is tested with an explicit `IS NOT NULL`, because a CHECK whose result is `NULL` counts as passed. `target_entrance > 0`. Covered by `tests/communication/test_models.py`.
- `ck_comm_posts_forum_whole_building`: a `FORUM_TOPIC` is always `ALL_BUILDING`.
- `ck_comm_posts_only_emergency_inactive`: only an `EMERGENCY` can have `is_active = FALSE`.
- Titles and bodies are never blank (`btrim(...) <> ''`).
- `ck_comm_posts_only_forum_deleted`: only a `FORUM_TOPIC` can be deleted; `ck_comm_posts_deleted_consistent`: `deleted_at` and `deleted_by_user_id` are set together.
- **One building per row chain.** `comm_posts` is `UNIQUE (id, building_id)`. Composite foreign keys guarantee that a comment is in its post's building, `(post_id, building_id)` → `comm_posts`, and that the author's context apartment of a post or comment is in that building, `(author_apartment_id, building_id)` → `property_apartments`. A `NULL` apartment is not checked.
- Not enforced by the database: that a targeted apartment id exists (JSONB has no foreign keys) and that the author belongs to the building. The service checks both on insert.
