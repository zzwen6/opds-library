# 推送到 GitHub 后立刻部署

推到 `main`（书库）或 `master`（豆瓣服务）后，GitHub Actions 会构建镜像并推到 GitHub Container Registry（GHCR），再调用你 NAS 上经 nginx 反向代理暴露的 Watchtower 接口，拉取新的 `:latest` 并重建容器。

```
git push
  → GitHub Actions 构建镜像（海外 runner，使用官方 npm / PyPI 源）
  → 推到 ghcr.io/zzwen6/opds-library 或 ghcr.io/zzwen6/douban-book-api
    （标签：latest、版本号（仅书库）、短 SHA）
  → POST/GET https://<你的 webhook 域名>/v1/update
    Header: Authorization: Bearer <DEPLOY_WEBHOOK_TOKEN>
  → NAS 上的 Watchtower 只更新带标签的 opds-library 和 douban-book-api
```

Watchtower 按镜像更新正在运行的容器，不会重新执行 `docker compose`。新增或改过的 compose 字段（例如新的环境变量、端口、卷）仍需在 NAS 上手动执行一次 `docker compose up -d`。

只改文档（`docs/**`、`*.md` 等）的提交不会触发构建，工作流会被跳过，这是正常的。需要强制构建时，在对应仓库的 Actions 里手动运行 **Release images**。

## 1. 第一次从 GHCR 拉镜像

第一次 Actions 跑完后，这两个包会出现在 GitHub 上：

- `ghcr.io/zzwen6/opds-library`（`opds-library` 是私有仓库，镜像默认也是私有的）
- `ghcr.io/zzwen6/douban-book-api`（仓库若是公开的，仍建议到包设置里确认可见性）

查看位置：仓库页面右侧 **Packages**，或打开 `https://github.com/zzwen6?tab=packages`。

若希望别人不用登录就能拉取豆瓣镜像，打开 `ghcr.io/zzwen6/douban-book-api` 的包设置，把可见性改为 Public。书库镜像建议保持私有。

### 创建拉取用的 PAT

在 <https://github.com/settings/tokens> 创建一个只用于 NAS 拉镜像的令牌（不要用可写代码的那个）：

- **Fine-grained token（推荐）**：Resource owner 选 `zzwen6`；在 Repository access 里只给 `opds-library`（若豆瓣镜像也是私有的，再加上 `douban-book-api`）；Permissions 里 Packages 选 Read，对私有仓库再给 Contents 选 Read。
- **Classic token**：勾选 `read:packages`。若要拉取与私有仓库关联的镜像，还需要 `repo`。不要勾选 `write:packages`，除非你还想在 NAS 上往 GHCR 推镜像。

把令牌妥善保存，只在 NAS 上用来 `docker login`。

### 在 NAS 上登录 GHCR

必须用**实际执行 `docker compose` 的同一个用户**登录，登录状态才能被 Compose 和 Watchtower 用到。群晖的 Container Manager 通常以 root 跑 Docker，这时请用 root（或 `sudo`）执行：

```bash
echo '你的PAT' | docker login ghcr.io -u zzwen6 --password-stdin
```

成功后会写出 `~/.docker/config.json`（root 则是 `/root/.docker/config.json`）。这个文件需要一直留着，重启 NAS 后也不必重新登录。

如果文件不在 `/root/.docker/config.json`，在 `.env` 里指定实际路径：

```
DOCKER_CONFIG_JSON=/root/.docker/config.json
```

Compose 会把它只读挂进 Watchtower 的 `/config.json`。Watchtower 不读取宿主机上的 `$HOME`，不挂这个文件就拉不了私有镜像。先完成 `docker login` 再 `compose up`，否则 Docker 可能把这个挂载路径建成一个空目录。

## 2. 在两个 GitHub 仓库里配置 Secrets

分别打开：

- https://github.com/zzwen6/opds-library/settings/secrets/actions
- https://github.com/zzwen6/douban-book-api/settings/secrets/actions

各添加两个 **Repository secrets**（两个仓库的值必须相同）：

| Name | Value |
| --- | --- |
| `DEPLOY_WEBHOOK_URL` | `https://<你的 webhook 域名>/v1/update` |
| `DEPLOY_WEBHOOK_TOKEN` | 与 NAS `.env` 里同一串令牌 |

令牌用下面命令生成，不要用示例里的占位符：

```bash
openssl rand -hex 32
```

Secrets 未配置时，Actions 仍会构建并推送镜像，但会跳过通知（日志里有说明），不会把工作流标失败。两项都配好后，若 webhook 返回非 2xx，工作流会失败。

## 3. 用 nginx 把 webhook 暴露到公网

给 Watchtower 单独配一个域名（或至少单独一条路径），不要和书库的 Web / OPDS 域名混在一起。GitHub Actions 请求的是 HTTPS，所以 nginx 上要配好证书。认证靠 Watchtower 的 Bearer 令牌，**不要**再给这个站点加 HTTP Basic、SSO 登录页或 Cloudflare Access，否则 Actions 过不去。

Watchtower 在 Compose 里默认只把 `8080` 映射到宿主机的 `127.0.0.1:8081`，局域网和公网网卡都访问不到。nginx 和 Docker 在同一台 NAS 上时，反代到这个回环地址即可。

```nginx
server {
    listen 443 ssl http2;
    server_name opds-deploy.example.com;   # 换成你的域名

    # ssl_certificate / ssl_certificate_key 按你现有证书配置

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_pass_header Authorization;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

群晖可以在「控制面板 → 登录门户 → 高级 → 反向代理」里加一条：来源 `HTTPS` + `opds-deploy.example.com` + `443`，目的地 `HTTP` + `127.0.0.1` + `8081`，并打开「WebSocket」以外的默认转发即可，注意不要开启「HSTS」以外、会丢掉 `Authorization` 头的额外鉴权。

如果 nginx 跑在 Docker 里，并且和书库在同一个 Compose 网络，把 `proxy_pass` 改成 `http://watchtower:8080`，并可以删掉 `watchtower` 服务的 `ports`。

如果 nginx 在**另一台机器**上，需要让它能访问到 NAS。把 compose 里 Watchtower 的端口改成只对 nginx 所在网段开放，例如 `192.168.1.10:8081:8080`（`192.168.1.10` 是 NAS 的内网地址），并在防火墙里只允许 nginx 那台机器访问 8081，不要把 8081 直接暴露到公网。

`DEPLOY_WEBHOOK_URL` 填（域名换成你的）：

```
https://opds-deploy.example.com/v1/update
```

在 NAS 上可以先本地试一下（把令牌换成真实值）：

```bash
curl -sS -D - -H "Authorization: Bearer 你的令牌" http://127.0.0.1:8081/v1/update
```

再从外网测一次同样的 HTTPS 地址。未带令牌或令牌错误时应失败；令牌正确时 Watchtower 会开始检查更新。

## 4. NAS 上的 `.env` 与启动

`.env` 放在 `docker-compose.yml` 旁边，不会进镜像，也不要提交到 Git。在已有配置上至少确认或补上这些行：

```
AUTH_PASSWORD=你的书库密码
LIBRARY_HOST_PATH=/volume1/books
DATA_HOST_PATH=./data
DOUBAN_CACHE_HOST_PATH=./douban-cache
PUID=1000
PGID=1000

OPDS_IMAGE=ghcr.io/zzwen6/opds-library:latest
DOUBAN_IMAGE=ghcr.io/zzwen6/douban-book-api:latest

DEPLOY_WEBHOOK_TOKEN=用openssl生成的那一串
DOCKER_CONFIG_JSON=/root/.docker/config.json
```

`PUID` / `PGID` 换成 NAS 上 `id` 的结果；书库路径换成实际目录；`DOCKER_CONFIG_JSON` 换成上一步 `docker login` 产生的 `config.json` 路径。

然后：

```bash
mkdir -p data douban-cache
docker compose up -d
```

第一次启动会按 `.env` 里的镜像名拉取 `:latest`。若私有镜像拉取失败，先检查 `docker login` 是否用对了用户、PAT 是否还有效。

## 5. 之后一次推送会经过哪些步骤

1. 你把代码推到对应仓库的默认分支（或在 Actions 里手动运行 **Release images**）。
2. Actions 构建镜像（Dockerfile 默认的国内源会被覆盖成官方 npm / PyPI），打上 `latest`、版本号（仅书库）和短 SHA，推到 GHCR。
3. 若两个 Secret 都已配置，Actions 请求 `DEPLOY_WEBHOOK_URL`，并带上 `Authorization: Bearer <DEPLOY_WEBHOOK_TOKEN>`。
4. nginx 把 HTTPS 请求转到本机的 Watchtower。
5. Watchtower 只处理带 `com.centurylinklabs.watchtower.enable=true` 标签的容器（`opds-library` 和 `douban-book-api`），拉取新的 `:latest`，用同一组参数重建容器，并清理旧镜像。Watchtower 自己不会被更新。
6. NAS 上已经在跑的容器在 webhook 到来之前保持不变。只推豆瓣仓库时，同样会通知 Watchtower，书库镜像若没有新的 `:latest` 则不会重建。

## 6. 仍然可以在本地构建

不想走 GHCR 时，照旧在本地构建，并在 `.env` 里覆盖镜像名：

```bash
docker build --build-arg PUID=1000 --build-arg PGID=1000 -t opds-library:0.5.6 .
docker build -t douban-book-api:zzwen6 https://github.com/zzwen6/douban-book-api.git#master
```

```
OPDS_IMAGE=opds-library:0.5.6
DOUBAN_IMAGE=douban-book-api:zzwen6
```

也可以构建后打上 `ghcr.io/zzwen6/opds-library:latest` 这样的标签，再 `docker push`。不配那两个 GitHub Secret，就不会触发远程 webhook。

Dockerfile 默认仍是国内 npm / pip 源，适合在国内机器上构建。GitHub Actions 里已经改回官方源，一般不用改。

## 7. 注意事项

- **第一次跑 Actions 时如果还没配 Secret，只会把镜像推到 GHCR，不会部署到 NAS。** 配好 Secret、nginx 反代、`.env` 并 `docker compose up -d` 之后，再推一次（或手动跑工作流）就会部署。
- **没做 `docker login` 时，私有镜像拉取会失败。** Watchtower 必须能读到宿主机的 Docker 凭证（见第 1 步的 `config.json` 挂载）。
- **GHCR 镜像目前是 `linux/amd64`。** 如果 NAS 是 ARM（部分群晖、大部分绿联/极空间），需要在本地按对应架构构建，或把工作流改成多架构构建。
- **Watchtower 只按镜像更新，不会重新应用 compose 的变更。** 改了 `docker-compose.yml` 或 `.env` 里的端口、路径、环境变量、新增服务等，要在 NAS 上再执行一次 `docker compose up -d`。
- **只改文档的提交不会构建镜像，也不会通知 Watchtower。**
- **轮换令牌：** 用 `openssl rand -hex 32` 生成新令牌 → 更新 NAS `.env` 里的 `DEPLOY_WEBHOOK_TOKEN` → `docker compose up -d watchtower`（只重建 Watchtower）→ 把两个仓库的 `DEPLOY_WEBHOOK_TOKEN` Secret 改成同一值。旧令牌随即失效。不要把令牌写进 Git。
- 本机开发（`python -m app.main`）不受影响，也不需要 Watchtower。
