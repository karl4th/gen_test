FROM python:3.13-slim

WORKDIR /app
ARG TARGETARCH
COPY docker/requirements-${TARGETARCH}.txt ./requirements-wheels.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-index --no-deps --retries 8 --timeout 60 \
    -r requirements-wheels.txt

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
ENV PYTHONPATH=/app/src

CMD ["uvicorn", "mdl_ml.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
