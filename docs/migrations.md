# Database migrations

The application and Alembic read `DATABASE_URL` from `.env`. An environment
variable with the same name overrides the value in that file. Run these
commands from the repository root.

Start the database:

```sh
docker compose up -d --wait
```

After adding a model, import it in `src/mdl_ml/models/__init__.py` so Alembic
can discover its tables. Then create and review a migration:

```sh
uv run alembic revision --autogenerate -m "describe schema change"
```

Apply migrations and inspect the database revision:

```sh
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
```

To reverse one revision locally:

```sh
uv run alembic downgrade -1
```

Review every generated migration before applying it. Reversing a migration
that removes data cannot restore that data automatically.

Run the database constraint checks against a migrated local database:

```sh
RUN_DB_TESTS=1 uv run python -m unittest discover -s tests -v
```

The test runs in a transaction and rolls back its sample rows.
