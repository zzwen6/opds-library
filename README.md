# 我的书库（OPDS Library）

自用的 Web 电子书管理站，并对外提供 **OPDS 1.2** 接口，可供 KOReader、Moon+ Reader、Panels、Thorium、Readest 等阅读器浏览和下载。

- 自建 SQLite 元数据库，**不读取、不写入 Calibre 的 `metadata.db` / OPF**
- 扫描 NAS 同机上的书库目录（默认只读），提取 EPUB / PDF / MOBI / AZW3 / CBZ 的内嵌元数据与封面
- 文件哈希去重，识别移动与重命名；启动后常驻监听（watchdog/inotify + 定时轮询兜底）
- 手动编辑元数据，字段级锁定，防止后续刮削覆盖
- 可插拔刮削器：内置 Open Library、Google Books，以及通过独立 HTTP 服务接入的豆瓣中文源。中文书优先豆瓣，其他优先 Open Library。高分或 ISBN 精确命中时自动写入未锁定字段，可在 Web 上撤销一次；也可批量刮削未处理书目
- 可选按模板整理、重命名书库文件（默认关闭；先预览再执行，可回滚）
- 批量编辑：多选书目，一次改作者、系列（可自动编号）、标签、语言、出版社；作者写法归并；先预览影响范围，跳过已锁定字段，可撤销（0.4.0）
- 重复书检测：文件哈希相同、ISBN 相同、书名+作者模糊匹配三类线索；可把同一本书的多种格式合并到一个条目，或标记“不是重复”；只改数据库，不删磁盘文件，合并可撤销（0.4.0）
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

书库目录默认以 **只读**（`:ro`）方式挂载。封面、缩略图和数据库写在 `./data`（或 `DATA_HOST_PATH`）。若要启用整理/改名，见下方说明。

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

启动时会自动建表，并为已有 SQLite 库补齐新增列（`upgrade_schema`）。也可用 Alembic：

```bash
alembic upgrade head
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LIBRARY_PATH` | `./library` | 书库根目录。默认按只读约定使用；整理功能开启时需要可写 |
| `DATA_PATH` | `./data` | SQLite、封面与缩略图 |
| `AUTH_USERNAME` | `admin` | Web / OPDS 共用 |
| `AUTH_PASSWORD` | `changeme` | 请务必修改 |
| `GOOGLE_BOOKS_API_KEY` | 空 | 可选。无 Key 时仍会尝试；遇到 429 会短时间退避，不影响其他刮削源 |
| `SCRAPE_AUTO_THRESHOLD` | `90` | 达到该分数（或 ISBN 精确命中）时自动写入未锁定字段 |
| `ORGANIZE_ENABLED` | `false` | 整理/改名开关，默认关闭 |
| `ORGANIZE_TEMPLATE` | `{author}/{series}/{title}.{ext}` | 整理命名模板 |
| `WATCH_ENABLED` | `true` | 启动后监听书库变化并自动扫描 |
| `WATCH_POLL_SECONDS` | `300` | 定时轮询兜底间隔（秒）。部分 NAS 挂载上 inotify 可能不可靠 |
| `DOUBAN_ENABLED` | `true` | 是否启用豆瓣中文刮削（通过独立的 douban-book-api） |
| `DOUBAN_API_URL` | `http://127.0.0.1:3000` | douban-book-api 的根地址。Compose 网络内请用 `http://douban-book-api:3000` |
| `DOUBAN_TIMEOUT_SECONDS` | `15` | 调用该服务的超时时间 |
| `DOUBAN_MIN_INTERVAL_SECONDS` | `3` | 两次请求之间的最短间隔，降低对上游的压力 |
| `DOUBAN_SEARCH_DETAIL_LIMIT` | `1` | 关键词搜索后，最多再请求几条 `/subject/:id` 补全简介 |
| `DOUBAN_COVER_BROWSER_HEADERS` | `true` | 仅对 `*.doubanio.com` 封面使用常见桌面浏览器请求头（User-Agent / Accept: image/* / Referer: https://book.douban.com/）。不含 cookie、不走代理。失败（418/403/超时）时保留已有封面且不重试 |
| `PAGE_SIZE` | `50` | OPDS 与 API 默认分页大小 |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | 监听地址 |

## 刮削

- 分数 ≥ `SCRAPE_AUTO_THRESHOLD`（默认 90），或 ISBN 精确一致时，自动写入**未锁定**字段。
- 低于阈值的候选进入「刮削确认」队列，需手动采用。
- 自动写入会记录字段级来源和旧值；可在书目详情页「撤销自动写入」一次，恢复写入前的值。
- 「刮削确认」页支持对 `scrape_status=pending` 的书目批量自动刮削。
- 保存手改字段仍会自动锁定，后续刮削（含自动写入）会跳过锁定字段。

## 豆瓣中文源

主项目**不抓取**豆瓣网页，只通过 HTTP 调用独立服务 [zzwen6/douban-book-api](https://github.com/zzwen6/douban-book-api)（基于 acdzh/douban-book-api 的修复版）的 `/isbn/:isbn`、`/search?text=`，必要时再请求 `/subject/:id` 补全详情。

本地开发时，先在另一进程启动该服务（默认 `http://127.0.0.1:3000`），再启动本项目。服务不可达时会跳过豆瓣，不影响 Open Library / Google Books。

Docker 可选启用同文件中的 `douban-book-api` 服务（从该 GitHub 仓库构建）：

```bash
# 在同一 Compose 网络里把 URL 指向服务名
echo 'DOUBAN_API_URL=http://douban-book-api:3000' >> .env
docker compose --profile douban up --build -d
```

中文书（书名等含汉字，或 ISBN 为 978-7）优先采用豆瓣候选；其他书优先 Open Library。豆瓣标签目前常为空；若 `original_title` 被误填成作者名，本项目会丢弃。下载 `*.doubanio.com` 封面时默认使用常见浏览器请求头（可用 `DOUBAN_COVER_BROWSER_HEADERS=false` 关闭）；失败时保留已有封面，不重试。封面写入仍尊重字段锁定与撤销。

## 整理书库文件（默认关闭）

整理只会 **移动/重命名** 文件，**不修改文件内容，不写回 OPF**。流程：在 Web「整理文件」页生成预览（dry-run）→ 确认后执行 → 数据库中的路径同步更新 → 需要时可按批次回滚。

启用步骤：

1. 设置 `ORGANIZE_ENABLED=true`。
2. **Docker 部署必须把书库挂载改为可写。** 编辑 `docker-compose.yml`，将默认的只读挂载：

   ```yaml
   - ${LIBRARY_HOST_PATH:-./library}:/library:ro
   ```

   改为（去掉 `:ro`）：

   ```yaml
   - ${LIBRARY_HOST_PATH:-./library}:/library
   ```

   然后 `docker compose up -d`。未改挂载时，即使打开开关也会因目录只读而拒绝执行。
3. 默认挂载仍是只读；不启用整理就不必改。

模板可用变量：`author`、`authors`、`series`、`series_index`、`title`、`subtitle`、`publisher`、`year`、`isbn`、`language`、`ext`、`format`、`filename`、`uuid`。非法字符会被替换，中文保留，空路径段省略，重名时追加 `(2)`、`(3)`…。

## 批量编辑与作者归并（0.4.0）

在「书目」页点「多选」，点选封面（或“全选本页 / 全选全部结果”），再点「批量编辑」：

- 可改字段：作者（替换 / 追加 / 移除）、标签（追加 / 移除 / 替换）、系列（可同时设置序号：不改 / 统一设为 / 按选择顺序自动编号）、语言、出版社。只改勾选的字段，留空表示清除。
- 先点「预览影响范围」，显示将修改多少本、多少处，以及因字段锁定被跳过的书；确认后才执行。
- **字段锁**：某本书的对应字段已锁定时，这本书的该字段跳过并在结果里列出，其它字段照常修改。
- 默认勾选「改过的字段加锁」，和单本手动编辑一致，避免之后的刮削覆盖。
- 每处修改都记录旧值、新值和原来源（`field_sources` 记为 `batch`）。在「作者归并 / 批量记录」页可整批撤销。撤销时如果某字段之后又被改过，保留新值并提示冲突，不会硬覆盖。

「作者归并 / 批量记录」页还提供作者归并：

- 自动列出“疑似同一作者”。规范化规则：全角转半角，忽略大小写、空格和标点，去掉开头的 `[美]`、`（英）`、`〔日〕` 等国籍括注，西文兼容「名 姓」与「姓, 名」。
- 也可手动多选作者写法，改成一个统一名称。作者字段已锁定的书会跳过。
- 旧写法记为目标作者的别名；没有书再引用的旧作者条目会被删除。撤销时会重新创建。

API：`POST /api/batch/preview`、`POST /api/batch/apply`、`GET /api/authors/variants`、`POST /api/authors/merge/preview`、`POST /api/authors/merge`、`GET /api/edit-batches`、`POST /api/edit-batches/{id}/undo`。

## 重复书检测与合并（0.4.0）

「重复书」页按置信度列出疑似重复分组：

| 类型 | 置信度 | 判定 |
| --- | --- | --- |
| 相同文件 | 100 | SHA-256 相同。扫描时同哈希的副本已挂在同一本书下，这里**只标记**，不会删除任何磁盘文件；OPDS 中只给出一个下载链接 |
| 相同 ISBN | 95 | 书目的 ISBN-13/10 和文件内提取到的 ISBN 统一换算为 ISBN-13 后相同 |
| 书名作者相近 | 55–80 | 书名规范化后相同，并且作者有交集（80）；作者信息缺失（60）；主书名相同、副标题不同（65）；同作者下书名相似度 ≥ 90%（55）。只作为建议 |

书名规范化会去掉书名号、括注（如 `（精校版）`、`[epub]`）、全角符号、标点和空白。

可以做的操作：

- **合并**：选一个主书目，勾选要并入的书，先「预览合并」再「确认合并」。其它书目的文件、封面、刮削记录会挂到主书目下，OPDS 条目随之提供多个格式的下载链接；被并入的书目条目从数据库删除，**磁盘文件不动**。
  - 主书目已锁定的字段保持不变。
  - 被并入书目中**已锁定**的字段，在主书目未锁定时优先采用，并在主书目上保持锁定。
  - 主书目为空的字段，用第一个有值的被并入书目补齐。
  - 标签取并集。
- **不是重复**：记住这个分组，之后不再提示。分组成员变化（例如新加入一本）后会重新提示。可勾选“显示已标记”后取消标记。
- **撤销合并**：在「批量记录」中撤销。被并入的书目按原 UUID 恢复（OPDS 标识不变），文件、封面、刮削记录、字段锁一并挂回。主书目被补齐的字段也会还原，若这些字段之后又改过则保留现状。

API：`GET /api/duplicates[?include_ignored=true]`、`POST /api/duplicates/ignore`、`POST /api/duplicates/unignore`、`POST /api/duplicates/merge/preview`、`POST /api/duplicates/merge`。

数据库升级：新增 `edit_batches`、`edit_changes`、`duplicate_ignores` 三张表，启动时自动创建，不改动已有表与数据。书目与文件本来就是一对多，合并直接复用这一关系。

## 文件监听

服务启动后默认开启监听：优先使用 watchdog/inotify，并按 `WATCH_POLL_SECONDS` 做定时轮询兜底。部分 NAS / 网络挂载无法可靠投递 inotify 事件，轮询可避免漏扫。测试或临时关闭可设 `WATCH_ENABLED=false`。

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
| `/opds/download/{file_id}` | 下载（`rel=acquisition`，**不是** open-access）。一本书有多种格式时给出多个下载链接；内容完全相同的副本只给一个 |
| `/opds/covers/{book_uuid}`、`/opds/thumbs/{book_uuid}` | 封面与缩略图 |

## 设计约定

- 高分或 ISBN 精确命中自动写入未锁定字段；低于阈值仍需人工确认。自动写入可撤销一次。
- 在 Web 中保存某字段会自动锁定该字段；刮削合并时跳过已锁定字段。
- 默认不把元数据写回原书文件。整理/改名默认关闭，需显式打开，并且只移动文件。
- 中文书元数据：通过独立的 douban-book-api 接入豆瓣（不在本进程抓网页）。也可继续只用 Open Library。对比与风险见 `docs/CHINESE_SOURCES.md`。
- Web 与 OPDS 共用一个 HTTP Basic 账号，不同步阅读进度。
- 批量编辑、作者归并、合并重复书全部只改数据库，每批可撤销；任何操作都不会删除书库中的文件。

## 备份

停止服务后，复制 `data/library.db`（建议同时复制 `data/library.db-wal`）以及 `data/covers/`。批量编辑与合并记录也保存在 `library.db` 中。也可以在 SQLite 中对运行中的数据库执行 `.backup`。若使用过整理功能，书文件本身也要以书库目录为准备份。
