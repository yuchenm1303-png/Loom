FROM python:3.12-slim

RUN useradd --create-home --uid 10002 loom \
    && mkdir -p /data /account-data \
    && chown loom:loom /data

WORKDIR /app
COPY services/__init__.py /app/services/__init__.py
COPY services/loom_model_policy/__init__.py services/loom_model_policy/catalog.py services/loom_model_policy/policy.py services/loom_model_policy/server.py /app/services/loom_model_policy/

ENV PYTHONUNBUFFERED=1 \
    LOOM_MODEL_POLICY_HOST=0.0.0.0 \
    LOOM_MODEL_POLICY_PORT=8792 \
    LOOM_MODEL_POLICY_DB=/data/model-policy.db \
    LOOM_ACCOUNT_DB_READONLY=/account-data/accounts.db

VOLUME ["/data"]
EXPOSE 8792
USER loom
CMD ["python", "-m", "services.loom_model_policy.server"]
