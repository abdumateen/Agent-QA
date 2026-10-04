# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m pip install "poetry==2.1.3"

COPY pyproject.toml ./
COPY agent_qa/ ./agent_qa/

RUN poetry build --format wheel \
    && python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install dist/*.whl \
    && /opt/venv/bin/python -m pip check

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HOME="/home/agentqa" \
    AGENT_QA_DB_PATH="/home/agentqa/.agent-qa/memory.db" \
    AGENT_QA_LOG_DIR="/home/agentqa/.agent-qa/logs"

RUN groupadd --gid 10001 agentqa \
    && useradd \
        --uid 10001 \
        --gid agentqa \
        --create-home \
        --home-dir /home/agentqa \
        --shell /usr/sbin/nologin \
        agentqa \
    && mkdir -p /home/agentqa/.agent-qa/logs \
    && chown -R agentqa:agentqa /home/agentqa

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/agentqa

USER agentqa:agentqa

RUN python -c "import sqlite3; connection = sqlite3.connect(':memory:'); connection.execute('CREATE VIRTUAL TABLE fts_probe USING fts5(content)'); connection.close()"

# Both listeners use loopback. Published container ports cannot reach them.
# Deployment must share the appropriate network namespace.
EXPOSE 8765 8766

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=12s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys; import httpx; client = httpx.Client(timeout=5.0, transport=httpx.HTTPTransport(retries=1), trust_env=False); response = client.get('http://127.0.0.1:8765/health'); response.raise_for_status(); healthy = response.json() == {'status': 'ok', 'version': '0.1.0'}; client.close(); sys.exit(0 if healthy else 1)"]

CMD ["agent-qa-core"]