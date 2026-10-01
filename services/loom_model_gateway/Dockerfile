FROM python:3.12-slim
WORKDIR /app
COPY services /app/services
ENV PYTHONUNBUFFERED=1
EXPOSE 8790
CMD ["python", "-m", "services.loom_model_gateway.server", "--host", "0.0.0.0", "--port", "8790"]
