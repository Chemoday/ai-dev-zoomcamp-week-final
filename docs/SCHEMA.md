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
| `meter_id` | `UUID` | `FK(meters_devices.id), INDEX` | Target meter |
| `period_id` | `UUID` | `FK(meters_periods.id), INDEX` | Accounting period |
| `value` | `NUMERIC(12, 3)`| `NOT NULL` | Current cumulative meter reading |
| `previous_value` | `NUMERIC(12, 3)`| `NOT NULL` | Snapshot of prior period's value (or `initial_value`) |
| `consumption_delta` | `NUMERIC(12, 3)`| `NOT NULL` | `value - previous_value` |
| `source` | `VARCHAR(32)` | `NOT NULL` | Enum: `RESIDENT`, `ADMIN_OVERRIDE`, `AUTO_ESTIMATED`, `IOT_WEBHOOK` |
| `is_anomaly_confirmed`| `BOOLEAN` | `NOT NULL, DEFAULT FALSE` | `TRUE` if user passed 2-step popup confirmation |
| `submitted_by_user_id`| `UUID` | `FK(core_users.id), NULLABLE`| User who submitted/modified (NULL if system `AUTO_ESTIMATED`) |
| `recorded_at` | `TIMESTAMPTZ` | `NOT NULL` | Timestamp of last modification |
| `UNIQUE` | `(meter_id, period_id)` | | **Strictly 1 active reading per meter per period** |

**Integrity constraints of `meters_*` tables** (enforced by PostgreSQL, so no code path can bypass them):
- Every enum column (`resource_type`, `unit`, `status`, `source`) has a named `CHECK (… IN (…))`.
- `meters_periods.period_code` must match `^[0-9]{4}-(0[1-9]|1[0-2])$`.
- `meters_devices`: `initial_value >= 0` and `max_monthly_delta > 0`.
- `meters_readings`: `value >= 0`, `previous_value >= 0`, and `consumption_delta = value - previous_value` (the stored delta can never disagree with the values).
- `meters_reading_logs` is append-only: a trigger rejects every `UPDATE` and `DELETE`.

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
| `author_user_id` | `UUID` | `FK(core_users.id)` | Internal author ID (masked before API output) |
| `author_apartment_id`| `UUID` | `FK(property_apartments.id), NULLABLE`| Context apartment for privacy badge resolution |
| `post_type` | `VARCHAR(32)` | `NOT NULL, INDEX` | Enum: `EMERGENCY`, `OFFICIAL_NOTICE`, `FORUM_TOPIC` |
| `category` | `VARCHAR(50)` | `NOT NULL, DEFAULT 'GENERAL'` | `'WATER_SHUTOFF'`, `'MAINTENANCE'`, `'GENERAL'`, `'MARKETPLACE'` |
| `title` | `VARCHAR(255)` | `NOT NULL` | Headline |
| `body` | `TEXT` | `NOT NULL` | Content |
| `target_scope` | `VARCHAR(32)` | `NOT NULL, DEFAULT 'ALL_BUILDING'`| Enum: `ALL_BUILDING`, `ENTRANCE`, `APARTMENT_LIST` |
| `target_entrance` | `INTEGER` | `NULLABLE` | Populated if `target_scope == 'ENTRANCE'` |
| `target_apartment_ids`| `JSONB` | `NULLABLE` | List of apartment UUIDs if `target_scope == 'APARTMENT_LIST'` |
| `is_active` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | For `EMERGENCY`: `TRUE` while emergency banner is active |
| `comments_enabled` | `BOOLEAN` | `NOT NULL, DEFAULT TRUE` | Board can disable comments on official notices |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL, INDEX` | Creation timestamp |

### Table: `comm_comments`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `UUID` | `PK` | Comment ID |
| `post_id` | `UUID` | `FK(comm_posts.id), INDEX` | Parent post |
| `author_user_id` | `UUID` | `FK(core_users.id)` | Internal user ID |
| `author_apartment_id`| `UUID` | `FK(property_apartments.id), NULLABLE`| Context apartment |
| `body` | `TEXT` | `NOT NULL` | Comment text |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL` | Timestamp |