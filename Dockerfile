FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
ENV PYTHONPATH=/app/src

CMD ["uvicorn", "mdl_ml.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
