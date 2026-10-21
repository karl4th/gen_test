# Generation service

An asynchronous generation-order service with token accounting in PostgreSQL.
It charges a user once, tries eligible provider legs in priority order, and
refunds once if no leg succeeds. The included `alpha` and `beta` services are
configurable fake providers.

## Run

```sh
cp .env.example .env
docker compose pull
docker compose up -d --wait
curl http://127.0.0.1:8000/health
```

The sample values are also Compose defaults, so `docker compose up` works
without a local `.env` file. The API and both fake providers use the same
prebuilt image, `ghcr.io/karl4th/gen_test:latest`; Compose only pulls it.
Set `IMAGE_TAG=sha-<full commit SHA>` to select the image built for a specific
commit.
Compose starts PostgreSQL 16, the API with four Uvicorn workers, and one
process for each fake provider. The API container applies Alembic migrations
before starting. The catalog migration creates `image-fast` (2 tokens: alpha
up to 4 references, then beta up to 10) and `video-long` (25 tokens: beta up
to 2, then alpha up to 1 with `skip_on_moderation`).

Users are test fixtures because registration is outside the task scope. To
create one, run:

```sh
docker compose exec -T postgres psql -U postgres -d postgres -c 'INSERT INTO users DEFAULT VALUES RETURNING id;'
```

Credit it with a signed `POST /webhooks/payments` event, then send
`POST /generate` with `user_id`, `model_slug`, `prompt`, and `ref_images`.
The API also exposes `GET /tasks/{id}` and `GET /users/{id}/balance`.
Provider webhooks go to `POST /webhooks/providers/{alpha|beta}`. Every webhook
uses `X-Signature`: the hexadecimal HMAC-SHA256 digest of the exact request
body, keyed by `WEBHOOK_SECRET` (`postgres` in the sample environment).

The fake providers offer `PUT /testing/config` on local ports 8001 and 8002.
The JSON fields are `failure_rate` (0–1), `failure_type` (`technical`,
`moderation`, `payload`), `sync_failure` (`503`, `timeout`, or null),
`delay_seconds` (null means random 2–20 seconds), `duplicate_webhooks`, and
`no_webhook`. They return HTTP 202 with an external ID before sending a
callback. The testing endpoint configures one provider process at a time.

## Tests and migrations

```sh
uv sync --locked --dev
RUN_DB_TESTS=1 uv run --no-sync python -m unittest discover -s tests -v
```

These tests use the compose API and real PostgreSQL. They cover 20 concurrent
copies of one payment, 10 concurrent final-leg failures, success on the second
leg, and a completion arriving after the watchdog has refunded the order.
The schema test also checks database constraints. See
[migration commands](docs/migrations.md) for applying and creating revisions;
see [schema](docs/schema.md) for the table-level invariants.
For local changes, build and start the stack from source before testing:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build --wait
RUN_DB_TESTS=1 uv run --no-sync python -m unittest discover -s tests -v
```

The GitHub Actions workflow checks Python syntax, Ruff lint and formatting,
strict mypy types, and builds the Python package. It then runs the integration
suite against PostgreSQL and the four-worker API. Only a successful push to
`master` or a `v*` tag publishes a multi-platform (`amd64` and `arm64`) image
to GHCR. The `master` build receives `latest` and `sha-<commit>` tags; version
tags also receive a versioned tag. Pull requests run checks without publishing.
The workflow publishes with the repository's `GITHUB_TOKEN`, so no personal
registry token is required. Before distributing the image, verify that its
GHCR package visibility is **Public** in GitHub package settings; repository
visibility alone does not set package visibility. The publish job tests an
anonymous pull and fails with a clear message if the package is private. Set
the package visibility to Public and rerun the job. Public GHCR images can be
pulled without signing in.

Run the static checks locally with `uv run --no-sync ruff check .`,
`uv run --no-sync ruff format --check .`, and `uv run --no-sync mypy`.

Python dependencies are locked in `uv.lock`. Docker installs wheel URLs with
SHA256 checks from `docker/requirements-{arm64,amd64}.txt`, avoiding index
resolution. After changing dependencies, refresh both exports:

```sh
uv export --format requirements-txt --no-dev --no-emit-project --no-hashes -o requirements.txt
uv run python scripts/export_docker_wheels.py
```

## Consistency rules

Tokens are integral `BIGINT` values. A payment is stored under its primary
key; only `succeeded` credits tokens. The payment row, ledger row, and balance
change commit in one transaction. A deferred PostgreSQL constraint trigger
also rejects a commit where a user's balance differs from that user's ledger
sum. A repeated identical webhook conflicts on
the primary key and has no effect. A changed event with the same payment ID
returns 409. Values outside the signed `BIGINT` range are rejected with 422.
A payment locks the user row and keeps room to refund every active task: if
the balance plus the active tasks' prices plus the payment would exceed the
`BIGINT` maximum, it returns 409 and rolls back without storing the payment.
Charges and refunds only move tokens between the balance and that reserve,
so a later refund cannot overflow. The ledger has a partial unique index on
payment IDs for payment entries.

Request fields have length limits, rejected with 422: payment and provider
external IDs up to 200 characters, prompts up to 10,000, at most 100
reference images of up to 2,048 characters each, and result URLs up to
2,048. Within that limit, a reference count no leg accepts still returns 400.

Generation locks the user row before checking the three-active-task limit
and applying a conditional debit. This serializes simultaneous orders for
one user. The task, debit, charge ledger entry, and first attempt commit
together. If no leg accepts the reference count, the request returns 400
before the debit. Insufficient balance returns 402; three active tasks return
429. Each task has at most one charge ledger row by a partial unique index.

Each callback, synchronous dispatch failure, and watchdog timeout uses the
same state transition. It locks the task row, checks that the attempt is still
current and the task is running, then conditionally updates the attempt and
task. A technical failure tries the next eligible leg. Moderation skips later
legs marked `skip_on_moderation`. Payload failure is terminal. When no leg
remains, the same transaction marks the task failed, inserts its refund ledger
row, and increments the balance. The partial unique refund index makes a
second refund impossible. Balance changes and ledger entries always commit
or roll back together; ledger rows cannot be updated or deleted.

Deadlines are `timestamptz` values calculated with PostgreSQL `now()`.
`PROVIDER_TIMEOUT` defaults to 60 seconds; `PROVIDER_HTTP_TIMEOUT_SECONDS`
limits only the initial POST to a provider.
Every API worker runs a watchdog; conditional attempt updates let only one
worker claim a pending dispatch. The watchdog handles each attempt
separately, so an error on one attempt is logged and does not stop the rest
of the batch. The attempt's external ID is fixed before
the HTTP request and remains stable for retries. If a worker dies after
committing a task but before dispatch, the watchdog finds its pending attempt.
If it dies after marking an attempt sent, the watchdog advances it when its
deadline passes. A process crash during a payment or provider webhook rolls
back the entire transaction; a retried webhook can then process it. A crash
after commit makes the replay a no-op. An HTTP timeout can leave an uncertain
provider request; the fixed external ID lets the fake provider recognize the
same attempt, while the database accepts only the current attempt's result.

**Late completion policy:** the first committed transition wins. If a
completion acquires the task lock before the watchdog, the task completes and
is not refunded. If the watchdog times out first, the attempt is no longer
current or the task is terminal. The callback stores its URL in
`late_result_url` for audit but cannot change the task status, result, or
balance. This avoids both a result and a refund for one order.

Authentication, UI, durable file storage, and real payments are outside the
scope of this service.
