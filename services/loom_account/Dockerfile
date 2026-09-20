FROM python:3.12-slim

WORKDIR /app
COPY services /app/services
ENV PYTHONUNBUFFERED=1 \
    LOOM_ACCOUNT_HOST=0.0.0.0 \
    LOOM_ACCOUNT_PORT=8787 \
    LOOM_ACCOUNT_DB=/data/accounts.db
VOLUME ["/data"]
EXPOSE 8787
CMD ["python", "-m", "services.loom_account.server"]
