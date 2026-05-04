# 金马珠宝 AI 生图系统

这是一个前后端分离的 AI 珠宝设计工作台，前端使用 React + TypeScript + Vite，后端使用 FastAPI，生图能力通过第三方模型平台接入，并支持历史记录、资产管理、OSS 图片存储等能力。

## 项目结构

```text
backend/
  app/
    api/v1/routes/      # 后端 API 路由
    core/               # 配置读取
    db/                 # 数据库连接
    models/             # 数据库模型
    schemas/            # 请求与响应结构
    services/           # 业务服务
  sql/                  # 数据库初始化 SQL
frontend/
  src/
    components/         # 前端组件
    pages/              # 页面
    services/           # API 请求
    styles/             # 样式
    types/              # TypeScript 类型
```

## 后端启动

推荐使用本项目的 `jinma_jewelry` conda 环境。后端 API 服务负责登录、鉴权、资产、历史、提交任务和查询任务状态。

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000


python -m app.worker
```

说明：

- `--host 0.0.0.0` 表示允许局域网其他设备访问后端。
- 如果只写 `python -m uvicorn app.main:app --reload`，默认会监听 `127.0.0.1:8000`，只能本机访问。
- 前端开发服务器现在会把 `/api` 请求代理到 `http://127.0.0.1:8000`，所以前后端在同一台机器上运行时也能正常工作。
- `python -m app.worker` 不是后端 API 服务，它只负责执行 Redis 队列里的任务，不能处理登录接口。

## Agent 服务启动

AI Agent 是同仓库的独立 FastAPI 服务，复用后端登录 Cookie、数据库、资产和任务队列。开发时建议和后端 API、Redis worker 一起启动：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m uvicorn agent_service.main:app --reload --host 0.0.0.0 --port 8010
```

前端开发服务器会把 `/agent-api` 代理到 `http://127.0.0.1:8010`。如果没有配置 `AGENT_LLM_API_KEY`，Agent 会使用本地规则兜底回复和动作卡，方便先验证流程。

## 前端启动

```bash
cd /home/chaihe/projects/jinma_jewelry_system/frontend
npm install
npm run dev
```

默认前端地址：

```text
http://localhost:5173
```

局域网访问示例：

```text
http://192.168.10.243:5173
```

这里的 `192.168.10.243` 需要替换成你运行前端那台电脑的局域网 IP。

## 局域网访问检查

如果局域网访问前端时提示后端不响应，按下面顺序检查。

### 1. 检查后端是否启动成功

后端终端应该看到类似日志：

```text
Uvicorn running on http://0.0.0.0:8000
Application startup complete.
```

如果看到的是：

```text
Uvicorn running on http://127.0.0.1:8000
```

说明后端只监听本机。虽然前端代理在同机运行时可以访问，但如果你想从局域网直接访问后端，建议改用：

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. 检查健康接口

在运行后端的电脑上访问：

```text
http://127.0.0.1:8000/health
```

如果使用 `--host 0.0.0.0` 启动，也可以在局域网设备上访问：

```text
http://192.168.10.243:8000/health
```

正常返回示例：

```json
{"status":"ok","environment":"development"}
```

### 3. 检查 Vite 代理错误

如果前端终端出现：

```text
[vite] http proxy error: /api/v1/ai/models
Error: connect ECONNREFUSED 127.0.0.1:8000
```

通常表示：

- 后端没有启动；
- 后端启动失败后退出了；
- 后端不是运行在前端同一台机器；
- 后端端口不是 `8000`；
- 前端启动后修改了代理配置但没有重启 Vite。

解决方式：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

然后重启前端：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/frontend
npm run dev
```

## 前端 API 地址说明

前端默认请求相对路径：

```text
/api/v1
```

开发环境下由 Vite 代理转发到：

```text
http://127.0.0.1:8000
```

如果前端和后端不在同一台机器，可以通过环境变量指定后端地址：

```powershell
$env:VITE_BACKEND_TARGET="http://192.168.10.243:8000"
npm run dev
```

如果需要让前端直接请求后端地址，也可以配置：

```powershell
$env:VITE_API_BASE_URL="http://192.168.10.243:8000/api/v1"
npm run dev
```

一般开发时推荐使用默认代理方式，不需要手动配置 `VITE_API_BASE_URL`。

## 配置文件说明

- 后端配置示例文件在 `backend/.env.example`。
- 后端实际运行配置通常写在 `backend/.env`。
- 后续新增任何配置项时，必须同步补到示例文件，并加中文注释，至少说明：
  - 配置用途
  - 单位
  - 默认建议值
  - 调大或调小的影响（如果该参数会明显影响性能或行为）

当前 Redis 轻量缓存相关配置如下：

```env
# 任务状态缓存保留时长，单位秒。前端轮询任务状态时优先读 Redis。
# 建议：开发环境 1800，内网生产 21600。
CACHE_JOB_STATUS_TTL_SECONDS=21600

# 重复提交去重缓存保留时长，单位秒。用于防止用户短时间重复点击提交。
# 建议：开发环境 60，内网生产 120。
CACHE_JOB_DEDUPE_TTL_SECONDS=120

# 模型列表缓存保留时长，单位秒。/api/v1/ai/models 会优先命中该缓存。
# 建议：开发环境 60，内网生产 300。
CACHE_MODEL_CATALOG_TTL_SECONDS=300

# 当前用户信息缓存保留时长，单位秒。/api/v1/auth/me 会优先命中该缓存。
# 建议：开发环境 30，内网生产 60。
CACHE_AUTH_ME_TTL_SECONDS=60
```

## 常用接口

```text
GET  /health
GET  /api/v1/system/summary
GET  /api/v1/ai/models
GET  /api/v1/ai/jobs/{job_id}
GET  /api/v1/history
GET  /api/v1/assets
POST /api/v1/ai/text-to-image
POST /api/v1/ai/fuse-images
POST /api/v1/ai/reference-image-transform
POST /api/v1/ai/multi-view
POST /api/v1/ai/split-multi-view
POST /api/v1/ai/jobs/text-to-image
POST /api/v1/ai/jobs/fuse-images
POST /api/v1/ai/jobs/reference-image-transform
POST /api/v1/ai/jobs/product-refine
POST /api/v1/ai/jobs/gemstone-design
POST /api/v1/ai/jobs/upscale
POST /api/v1/ai/jobs/multi-view
POST /api/v1/ai/jobs/split-multi-view
```

## 单机并发部署

当前阶段不需要额外引入 Nginx 做负载均衡。单机内网部署时，需要分别启动 Redis、Web API 服务、队列 Worker。

如果只是想把前端静态站点和 `/api` 收口到同一个访问入口，现在已经补了一版基础 `nginx` 配置，位置在：

- [jinma.conf](/home/chaihe/projects/jinma_jewelry_system/deploy/nginx/jinma.conf)
- [jinma.docker.conf](/home/chaihe/projects/jinma_jewelry_system/deploy/nginx/jinma.docker.conf)
- [deploy/nginx/Dockerfile](/home/chaihe/projects/jinma_jewelry_system/deploy/nginx/Dockerfile)
- [deploy/nginx/README.md](/home/chaihe/projects/jinma_jewelry_system/deploy/nginx/README.md)

这版 `nginx` 当前只做两件事：

- 托管前端 `frontend/dist`
- 反向代理 `/api` 和 `/health` 到 `127.0.0.1:8000`

### 本机安装与启动 nginx

如果本机还没有安装 `nginx`，先执行：

```bash
sudo apt update
sudo apt install -y nginx
```

安装后，把项目里的配置拷贝到系统目录：

```bash
sudo cp /home/chaihe/projects/jinma_jewelry_system/deploy/nginx/jinma.conf /etc/nginx/conf.d/jinma.conf
```

校验配置是否正确：

```bash
sudo nginx -t
```

启动 nginx：

```bash
sudo systemctl enable nginx
sudo systemctl start nginx
```

如果 nginx 已经启动过，修改配置后用下面命令重载：

```bash
sudo systemctl reload nginx
```

查看运行状态：

```bash
systemctl status nginx
```

如果只是临时重启服务，也可以用：

```bash
sudo systemctl restart nginx
```

## Docker 单机部署

当前推荐的部署方式是：

- 单机服务器
- 局域网访问
- 图片默认走本地磁盘存储
- MySQL 只存 `storage_url` 这类逻辑地址，不存图片二进制

当前 Docker 方案的核心目录和文件：

- [docker-compose.yml](/home/chaihe/projects/jinma_jewelry_system/docker-compose.yml)
- [docker-compose.build.yml](/home/chaihe/projects/jinma_jewelry_system/docker-compose.build.yml)
- [backend.Dockerfile](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/backend.Dockerfile)
- [.env.docker.example](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/.env.docker.example)
- [wait_for_tcp.py](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/wait_for_tcp.py)

### 图片与数据如何存

- MySQL 中只存图片逻辑地址，例如 `local://generated/upscale/2026/04/xxx.png`
- 真实图片文件写在后端本地目录，即容器内的 `/app/backend/data/local_assets`
- 前端统一通过 `/api/v1/assets/content?storage_url=local://...` 读取图片
- `backend` 和 `worker` 共用同一个 `backend_data` 卷，所以 worker 生成的图片，backend 可以立即读取

### 第一次部署

1. 安装 Docker 和 Docker Compose 插件
2. 在仓库根目录复制环境变量模板：

```bash
cp deploy/docker/.env.docker.example .env.docker
```

3. 编辑 `.env.docker`，至少修改这些值：

- `BACKEND_IMAGE`
- `NGINX_IMAGE`
- `MYSQL_ROOT_PASSWORD`
- `AUTH_SECRET_KEY`
- `ROOT_DEFAULT_PASSWORD`
- `WUYIN_API_KEY`
- `APP_PUBLIC_BASE_URL`
- `APIYI_API_KEY` 或 `TTAPI_API_KEY`

4. 从 Docker Hub 拉取并启动容器：

```bash
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

5. 查看状态：

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f worker
```

### 验证是否启动成功

健康检查：

```bash
curl http://127.0.0.1:${NGINX_PORT:-80}/health
```

浏览器访问：

```text
http://<服务器IP>:<NGINX_PORT>
```

如果 `NGINX_PORT=80`，就直接访问：

```text
http://<服务器IP>
```

### 容器和卷说明

这套 Compose 默认会启动：

- `mysql`
- `redis`
- `backend`
- `worker`
- `nginx`

其中持久化卷至少有：

- `mysql_data`
- `backend_data`
- `redis_data`

最重要的是：

- `backend_data` 保存本地图片和后端本地数据目录
- 只要这个卷没删，重建 `backend` / `worker` 容器后，图片不会丢

### 后续升级

在目标机器上更新镜像后执行：

```bash
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

### 本机构建并推送 Docker Hub

如果你要发布新版本到 Docker Hub，先在本机构建镜像：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.build.yml build
```

登录 Docker Hub：

```bash
docker login
```

推送镜像：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.build.yml push backend nginx
```

说明：

- `worker` 和 `backend` 共用同一个后端镜像，所以只需要推一次 `BACKEND_IMAGE`
- 新机器部署时只需 `pull + up`，不再本地构建

### U 盘离线部署包

如果你想把整套程序放到 U 盘，新机器除了安装 Docker 之外什么都不下载，可以直接生成离线部署包。

生成离线包：

```bash
chmod +x deploy/docker/prepare_offline_bundle.sh
deploy/docker/prepare_offline_bundle.sh
```

如果你想自定义镜像名：

```bash
BACKEND_IMAGE=jinma-backend:offline \
NGINX_IMAGE=jinma-nginx:offline \
deploy/docker/prepare_offline_bundle.sh
```

如果 Python 依赖下载很慢，可以在 `.env.docker` 或命令前切换 `PIP_INDEX_URL`，默认使用清华 PyPI 源：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
deploy/docker/prepare_offline_bundle.sh
```

脚本会在 `dist/offline_bundle` 下生成：

- `jinma-images.tar`
- `.env.docker`
- `docker-compose.yml`
- `start_offline_stack.sh`
- `stop_offline_stack.sh`

把整个 `dist/offline_bundle` 目录拷到 U 盘即可。

新机器上使用：

```bash
cd offline_bundle
chmod +x start_offline_stack.sh
./start_offline_stack.sh
```

停止：

```bash
./stop_offline_stack.sh
```

这套离线包默认包含：

- 你的后端镜像
- 你的 nginx 前端镜像
- `mysql:8.4`
- `redis:7-alpine`

所以新机器不需要再联网拉镜像。

离线包里的 Compose 项目名固定为 `jinma_jewelry_system`。这样后续即使你把目录命名为 `offline_bundle_v1`、`offline_bundle_v2`，也会继续复用同一套 Docker volume：

- `jinma_jewelry_system_mysql_data`
- `jinma_jewelry_system_backend_data`
- `jinma_jewelry_system_redis_data`

更新时不要执行 `docker compose down -v`，否则会删除这些 volume，数据库和本地图片都会丢。

### 离线包备份与恢复

如果你要备份当前离线包运行中的数据库和本地图片，可以直接使用脚本：

```bash
chmod +x deploy/docker/backup_offline_stack.sh
deploy/docker/backup_offline_stack.sh
```

默认会在 `dist/offline_backup` 下生成：

- `jinma.sql`
- `backend_data.tar.gz`

如果你要自定义备份目录：

```bash
deploy/docker/backup_offline_stack.sh /path/to/backup_dir
```

恢复时使用：

```bash
chmod +x deploy/docker/restore_offline_stack.sh
deploy/docker/restore_offline_stack.sh
```

默认会从：

- `dist/offline_backup`
- `dist/offline_bundle`

读取备份和离线包。

如果你要手动指定目录：

```bash
deploy/docker/restore_offline_stack.sh /path/to/backup_dir /path/to/offline_bundle
```

恢复流程会自动：

- 启动离线包容器
- 导入 `jinma.sql`
- 恢复 `backend_data.tar.gz`
- 重启整套服务

建议长期保留这些文件：

- `jinma-images.tar`
- `.env.docker`
- `docker-compose.yml`
- `jinma.sql`
- `backend_data.tar.gz`

### 迁移到另一台机器

迁移时必须一起迁这两部分：

- MySQL 数据
- `backend_data` 卷中的本地图片

常见做法：

- 数据库用 `mysqldump`
- 图片数据直接打包卷对应目录或导出 Docker volume

恢复后只要数据库中的 `local://...` 不变，且新机器上的卷仍挂到 `/app/backend/data`，历史记录和资产就能继续访问。

### 1. 启动 Redis

```bash
redis-server
```

如果 Redis 已经作为系统服务启动，并监听 `127.0.0.1:6379`，这一步可以跳过。

### 2. 启动 Web API 服务

开发调试建议先用单 worker + reload：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

多人内网测试或准生产环境，当前这台机器推荐使用 `2` 个 Web worker：

- CPU: Intel i5-12400，`6` 核 `12` 线程
- 内存: `15GiB`
- 推荐起步值: `2 web worker + 2 queue worker`

启动命令：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 2
```

### 3. 启动队列 Worker

队列 Worker 负责执行 AI 生成任务、上传 OSS、写资产和历史记录。它不提供 Web API，也不能处理登录接口。当前这台机器推荐先启动 `2` 个 Queue Worker。

Worker 1：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m app.worker
```

Worker 2：

```bash
cd /home/chaihe/projects/jinma_jewelry_system/backend
conda activate jinma_jewelry
python -m app.worker
```

如果后续压测发现：

- Web 接口比较轻松
- 主要瓶颈在任务排队
- 上游模型平台也允许更高并发

可以再尝试增加到 `3` 个 Queue Worker，但不建议一开始直接开到 `4+`。

### 4. 启动前端

```bash
cd /home/chaihe/projects/jinma_jewelry_system/frontend
npm run dev
```

说明：

- `python -m uvicorn app.main:app ...` 或 `python -m gunicorn ...` 是后端 API 服务，必须启动，否则登录和所有 `/api` 接口都不可用。
- `python -m app.worker` 是队列消费者，只执行 Redis 里的任务，不能单独作为后端服务使用。
- 当前这台机器推荐并发配置：`2 web worker + 2 queue worker`。
- 旧同步生图接口仍保留，可用于回滚和对比。
- 新任务接口会立即返回 `job_id`，再通过 `GET /api/v1/ai/jobs/{job_id}` 查询 `queued`、`running`、`uploading`、`succeeded`、`failed`。
- 普通用户默认最多同时 1 个任务，root 默认最多 3 个任务，可通过 `QUEUE_USER_MAX_ACTIVE_JOBS` 和 `QUEUE_ROOT_MAX_ACTIVE_JOBS` 调整。
- 如果后续扩到多台服务器，再在前面加 Nginx 或其他反向代理做多机负载均衡。

## 数据库初始化

MySQL 初始化文件：

```text
backend/sql/init_mysql.sql
```

当前数据库配置在：

```text
backend/.env
```

示例：

```text
DATABASE_URL=mysql+pymysql://root:123456@192.168.10.150:3306/jinma
```

## 运行顺序建议

1. 启动 MySQL，确认 `jinma` 数据库存在。
2. 启动 Redis，确认监听 `127.0.0.1:6379`。
3. 开发调试时启动 Web API 服务：`python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`。
4. 内网测试或准生产时建议改用：`python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 2`。
5. 访问 `http://127.0.0.1:8000/health` 确认后端正常。
6. 启动 2 个队列 Worker：`python -m app.worker`。
7. 启动前端：`npm run dev`。
8. 访问 `http://192.168.10.243:5173` 进行局域网调试。

## 当前主要功能

- 文生图
- 多图融合
- 生成多视图
- 多视图切图
- 线稿转写实图
- 转灰度图
- 历史记录
- 资产管理
