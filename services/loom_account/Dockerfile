FROM python:3.12-slim

# The service is a stdlib HTTP server over SQLite. It needs no package installs
# and no root: it reads its own source and writes one database under /data.
RUN useradd --create-home --uid 10001 loom \
    && mkdir -p /data \
    && chown loom:loom /data

WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/loom_account/__init__.py services/loom_account/server.py services/loom_account/search.py services/loom_account/agent_ops.py /app/services/loom_account/

ENV PYTHONUNBUFFERED=1 \
    LOOM_ACCOUNT_HOST=0.0.0.0 \
    LOOM_ACCOUNT_PORT=8787 \
    LOOM_ACCOUNT_DB=/data/accounts.db
VOLUME ["/data"]
EXPOSE 8787

USER loom
CMD ["python", "-m", "services.loom_account.agent_ops"]
