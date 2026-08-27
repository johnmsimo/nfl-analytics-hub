FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    DATA_DIR=/app/data \
    SEED_DATA_DIR=/app/seed-data

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
RUN test -s /app/data/player_week_2025.csv \
    && mkdir -p /app/seed-data \
    && cp -a /app/data/. /app/seed-data/ \
    && test -s /app/seed-data/player_week_2025.csv \
    && rm -rf /app/data \
    && mkdir -p /app/data \
    && chown -R app:app /app

USER app
EXPOSE 8080

CMD ["gunicorn", "app:app", "-c", "gunicorn_conf.py"]
