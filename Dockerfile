# syntax=docker/dockerfile:1

# ---- 前端构建 ----
FROM node:22-slim AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- Python 依赖构建：只把运行依赖装进独立 venv ----
# 项目代码不 pip install，运行镜像直接从 /app 运行，
# 这样 app/main.py 按 __file__ 定位的 /app/web/dist 与 alembic 目录都保持不变。
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > requirements.txt
# 装完依赖后移除运行时不需要的 pip/setuptools/wheel（pip 缓存走 BuildKit cache mount，不进镜像）
# hadolint ignore=DL3042
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt \
    && pip uninstall -y pip setuptools wheel \
    && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---- 运行镜像 ----
FROM python:3.12-slim
ARG PUID=1000
ARG PGID=1000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    LIBRARY_PATH=/library \
    DATA_PATH=/data
# PGID 可能已被系统组占用（如群晖的 100=users），已存在就直接复用
RUN (getent group "${PGID}" >/dev/null || groupadd -g "${PGID}" app) \
    && useradd -l -o -u "${PUID}" -g "${PGID}" -M -d /app -s /usr/sbin/nologin app \
    && mkdir -p /data /library \
    && chown "${PUID}:${PGID}" /data
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY --from=frontend /web/dist ./web/dist
USER app
EXPOSE 8080
CMD ["python", "-m", "app.main"]
