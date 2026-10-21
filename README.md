# MDL ML

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
