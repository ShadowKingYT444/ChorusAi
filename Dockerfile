FROM python:3.11-slim

WORKDIR /app

# Unbuffered stdout/stderr so Railway logs show uvicorn/boot lines immediately.
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY Chorus/pyproject.toml pyproject.toml
COPY Chorus/orchestrator/ orchestrator/

RUN pip install --no-cache-dir . && mkdir -p /data && chmod 0777 /data

ENV CHORUS_DB_PATH=/data/chorus.db \
    ORC_EMBEDDING_BACKEND=hash \
    ORC_LAN_MODE=0 \
    ORC_KEY_PATH=/data/orchestrator_ed25519.key

EXPOSE 8080

# Railway injects $PORT; fall back to 8080 for local `docker run` and in case it's absent.
# The `echo` line ensures we see the resolved port in Railway logs even if uvicorn's own
# banner is buffered. `exec` replaces the shell so SIGTERM reaches uvicorn cleanly.
CMD ["sh", "-c", "PORT=\"${PORT:-8080}\"; echo \"[boot] uvicorn binding 0.0.0.0:${PORT}\"; exec uvicorn orchestrator.main:app --host 0.0.0.0 --port \"${PORT}\""]
