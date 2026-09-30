FROM node:22-bookworm-slim AS web
WORKDIR /src/desktop-react
COPY desktop-react/package.json ./package.json
RUN npm install --no-audit --no-fund
COPY desktop-react ./
RUN npm run build:electron && npm run build:web

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOOM_WEB_STATIC_DIR=/srv/loom-web/static \
    LOOM_WEB_RUNTIME_ROOT=/var/lib/loom-web \
    LOOM_WEB_APP_SERVER_SCRIPT=/opt/loom/loom_app_server.py \
    LOOM_WEB_PYTHONPATH=/opt/loom

WORKDIR /opt/loom
COPY pyproject.toml README.md ./
COPY loom_cli.py loom_app_server.py loom_web.py loom_chatgpt_mcp.py loom_model_bridge.py ./
COPY app ./app
RUN pip install --no-cache-dir .

WORKDIR /srv/loom-web
COPY services/loom_web_gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/loom_web_gateway/app.py services/loom_web_gateway/runtime.py ./
COPY --from=web /src/desktop-react/dist ./static

RUN useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin loomweb \
    && mkdir -p /var/lib/loom-web \
    && chown -R loomweb:loomweb /var/lib/loom-web /srv/loom-web
USER loomweb
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/api/healthz', timeout=3).read()" || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8790", "--ws-max-size", "67108864", "--proxy-headers", "--forwarded-allow-ips=*"]