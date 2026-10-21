# Core schema

The first migration creates exactly seven application tables: `users`,
`ledger`, `payments`, `models`, `legs`, `tasks`, and `attempts`. Alembic also
maintains its own `alembic_version` table.

`users.id`, `ledger.id`, and `legs.id` are generated `BIGINT` identities.
`tasks.id` and `attempts.id` are UUIDs with database defaults. The application
supplies them at order creation so an attempt can use its UUID as a stable
provider external ID.
`payments.payment_id` and `models.slug` are text keys. All timestamps use
timezone-aware PostgreSQL timestamps.

The ledger's partial unique indexes allow at most one payment entry per
payment ID, one charge per task, and one refund per task. A check constraint
requires the matching ID to be present and the delta to have the correct
sign. The payment primary key rejects a repeated payment ID. The attempts
table rejects a repeated task/leg pair or provider/external ID pair.
Composite foreign keys also require each ledger entry's user to own its
payment or task. A database trigger rejects `UPDATE`, `DELETE`, and `TRUNCATE`
on `ledger`, making journal entries append-only.
Deferred constraint triggers on users and ledger require each balance to
equal its ledger sum when a transaction commits. An index on
`ledger (user_id, id)` keeps that per-user sum and the latest-entries page
from scanning the whole journal, and an index on
`attempts (status, deadline_at)` serves the watchdog's deadline polling.

`tasks.current_attempt_id` is optional and, when set, must refer to an
attempt belonging to that task. `ref_images` must be a JSON array. Prices
and payment token amounts must be positive; user balances cannot be negative.

Task status transitions must use a conditional `UPDATE` that includes the
expected prior state. Three separate Python status enums define the allowed
values for payments, tasks, and attempts. Named PostgreSQL `CHECK` constraints
reject unknown values even for direct database writes. The current sets are:

- Payments: `succeeded`, `failed`.
- Tasks: `pending`, `running`, `completed`, `failed`.
- Attempts: `pending`, `sent`, `succeeded`, `failed`, `timed_out`.

`completed` and `failed` are terminal task statuses. Payment credit is allowed
only for `succeeded`; a `failed` payment is retained without credit. The
constraints validate values, while conditional updates enforce task
transitions and set `updated_at` explicitly.
