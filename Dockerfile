FROM node:22-slim AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBRARY_PATH=/library \
    DATA_PATH=/data
COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY alembic ./alembic
RUN pip install --no-cache-dir .
COPY --from=frontend /web/dist ./web/dist
EXPOSE 8080
CMD ["python", "-m", "app.main"]
