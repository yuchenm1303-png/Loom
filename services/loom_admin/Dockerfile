FROM nginx:1.27-alpine
COPY services/loom_admin/nginx.conf /etc/nginx/conf.d/default.conf
COPY services/loom_admin/static/ /usr/share/nginx/html/
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --retries=4 CMD wget -q -O - http://127.0.0.1:8800/healthz >/dev/null || exit 1
