# 我的书库（OPDS Library）

自用的 Web 电子书管理站，并对外提供 **OPDS 1.2** 接口，可供 KOReader、Moon+ Reader、Panels、Thorium、Readest 等阅读器浏览和下载。

- 自建 SQLite 元数据库，**不读取、不写入 Calibre 的 `metadata.db` / OPF**
- 扫描 NAS 同机上的书库目录（默认只读），提取 EPUB / PDF / MOBI / AZW3 / CBZ 的内嵌元数据与封面
- 文件哈希去重，识别移动与重命名
- 手动编辑元数据，字段级锁定，防止后续刮削覆盖
- 可插拔刮削器：内置 Open Library 与 Google Books；确认后才写入
- Web 与 OPDS 共用一套 HTTP Basic 账号（环境变量配置）

## 快速开始（Docker）

```bash
cp .env.example .env          # 务必修改 AUTH_PASSWORD
# 把书放到 ./library，或在 .env / compose 中设置 LIBRARY_HOST_PATH=/your/nas/books
docker compose up --build -d
```

然后访问：

- Web 管理：http://localhost:8080/ （用 `AUTH_USERNAME` / `AUTH_PASSWORD` 登录）
- OPDS 根目录：http://localhost:8080/opds
- OpenSearch：http://localhost:8080/opds/search.xml
- API 文档：http://localhost:8080/api/docs

在阅读器里填写 OPDS 地址 `http://<主机>:8080/opds`，认证方式选择 HTTP Basic。若要暴露到外网，请放在 HTTPS 反代后面；不要在明文 HTTP 上使用 Basic 认证出网。

书库目录以 **只读** 方式挂载。封面、缩略图和数据库写在 `./data`（或 `DATA_HOST_PATH`）。

## 本地开发

需要 Python 3.12+（3.13 也可）、Node.js 20+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cd web && npm install && npm run build && cd ..

export LIBRARY_PATH=./library DATA_PATH=./data
export AUTH_USERNAME=admin AUTH_PASSWORD=changeme
python -m app.main
```

开发前端时可另开终端：`cd web && npm run dev`（Vite 会把 `/api` 与 `/opds` 代理到 8080 端口）。

下载公有领域样书：

```bash
python scripts/download_samples.py ./library
```

运行测试：

```bash
pytest -q
```

数据库迁移（可选，首次启动也会 `create_all`）：

```bash
alembic upgrade head
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LIBRARY_PATH` | `./library` | 书库根目录，服务按只读约定使用 |
| `DATA_PATH` | `./data` | SQLite、封面与缩略图 |
| `AUTH_USERNAME` | `admin` | Web / OPDS 共用 |
| `AUTH_PASSWORD` | `changeme` | 请务必修改 |
| `GOOGLE_BOOKS_API_KEY` | 空 | 可选；官方文档要求公开查询携带 Key，无 Key 时仍会尝试，可能受配额限制 |
| `PAGE_SIZE` | `50` | OPDS 与 API 默认分页大小 |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | 监听地址 |

## OPDS 端点（1.2 Atom）

| 路径 | 类型 |
| --- | --- |
| `/opds` | 导航根 |
| `/opds/new` | 最新入库（获取馆） |
| `/opds/books` | 全部书目 |
| `/opds/authors`、`/opds/authors/{id}` | 作者导航与列表 |
| `/opds/series`、`/opds/series/{id}` | 丛书 |
| `/opds/tags`、`/opds/tags/{id}` | 标签 |
| `/opds/search.xml` | OpenSearch 描述 |
| `/opds/search?q=` | 搜索获取馆 |
| `/opds/download/{file_id}` | 下载（`rel=acquisition`，**不是** open-access） |
| `/opds/covers/{book_uuid}`、`/opds/thumbs/{book_uuid}` | 封面与缩略图 |

## 设计约定

- 刮削默认需要人工确认后才写入，即使 ISBN 精确匹配也不自动覆盖。
- 在 Web 中保存某字段会自动锁定该字段；刮削合并时跳过已锁定字段。
- 默认不把元数据写回原书文件，也不整理、重命名书库里的文件。
- 中文刮削源（如豆瓣）仅保留插件接口，本阶段不实现。

## 备份

停止服务后，复制 `data/library.db`（建议同时复制 `data/library.db-wal`）以及 `data/covers/`。也可以在 SQLite 中对运行中的数据库执行 `.backup`。
