# MDL ML

A generation-order service backed by PostgreSQL. The database schema and
health endpoint are available; payment processing, task execution, provider
fallback, and the watchdog are the next application components.

Start PostgreSQL and the API from the repository root:

```sh
docker compose up -d --wait
uv run mdl-ml
```

`GET http://127.0.0.1:8000/health` checks the application and its database
connection. It returns HTTP 503 if the database is unavailable.

Database schema changes are managed with Alembic. See
[migration commands](docs/migrations.md) for creating and applying revisions,
and the [schema overview](docs/schema.md) for table guarantees.

## Task assumptions

- Tokens are whole numbers stored as `BIGINT`.
- After a moderation rejection, provider legs marked `skip_on_moderation`
  are skipped; execution continues with the next eligible leg.
- A payment credits tokens only when its status is `succeeded`. A `failed`
  payment is stored without a credit.
- Database timestamps use `timestamptz`. Deadline calculations use the
  database's `now()`, not a worker's clock.
- `completed` and `failed` are terminal task statuses; a task never leaves
  either state.
- PostgreSQL unique keys, conditional updates, row locks, and transactions
  enforce correctness across repeated events and four workers. An application
  existence check does not replace a database constraint.
- Ledger entries are append-only and are the source of truth for token
  movements.

Authentication, a user interface, persistent file storage, and real-money
payments are outside this project's scope.
