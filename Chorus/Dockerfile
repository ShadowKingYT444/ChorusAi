FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY orchestrator/ orchestrator/

RUN pip install --no-cache-dir . && mkdir -p /data

ENV CHORUS_DB_PATH=/data/chorus.db \
    ORC_EMBEDDING_BACKEND=hash \
    ORC_LAN_MODE=0 \
    PORT=8000

EXPOSE 8000

CMD uvicorn orchestrator.main:app --host 0.0.0.0 --port ${PORT}
