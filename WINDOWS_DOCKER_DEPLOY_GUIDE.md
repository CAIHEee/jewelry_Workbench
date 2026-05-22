# Windows Docker 部署指南

本文面向 Windows 10 / Windows 11 机器，目标是把 `jinma_jewelry_system` 用 Docker 方式完整部署起来，并把 Windows 侧常见环境问题一次处理清楚。

适用场景：

- Windows 单机部署
- 局域网访问
- 使用 Docker Desktop
- 后端图片默认存本地卷
- MySQL 只存图片逻辑地址，不存图片二进制

对应项目文件：

- [README.md](/home/chaihe/projects/jinma_jewelry_system/README.md:314)
- [docker-compose.yml](/home/chaihe/projects/jinma_jewelry_system/docker-compose.yml:1)
- [deploy/docker/.env.docker.example](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/.env.docker.example:1)
- [deploy/docker/README.offline.md](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/README.offline.md:1)

## 1. 先确认 Windows 机器是否适合部署

建议最低配置：

- Windows 10 22H2 或 Windows 11
- 8GB 内存可运行，16GB 更稳
- 系统盘至少预留 20GB，可用空间越多越好
- CPU 支持虚拟化
- 能正常联网访问 Docker Hub 和你配置的 AI 平台

不建议把项目放在这些位置：

- 桌面
- 下载目录
- OneDrive 同步目录
- 中文路径很深的目录

推荐目录：

```text
C:\projects\jinma_jewelry_system
```

或者：

```text
D:\projects\jinma_jewelry_system
```

## 2. 开启 BIOS 虚拟化

Docker Desktop 依赖硬件虚拟化。若没开，后续 WSL2 和 Docker 都会出问题。

检查方式：

1. 打开任务管理器
2. 切到“性能”
3. 点“CPU”
4. 看右下角“虚拟化”

如果显示“已启用”，继续下一步。  
如果显示“已禁用”，需要重启电脑进入 BIOS/UEFI 开启：

- Intel 常见名称：`Intel Virtualization Technology`、`VT-x`
- AMD 常见名称：`SVM Mode`

开启后保存并重启 Windows。

## 3. 安装 WSL2

推荐用管理员权限打开 PowerShell，执行：

```powershell
wsl --install
```

执行后通常需要重启电脑。

重启后再检查：

```powershell
wsl -l -v
```

如果能看到发行版，并且版本是 `2`，说明 WSL2 正常。

如果提示命令不存在，通常是 Windows 版本较旧，需要先更新系统。  
如果提示虚拟机平台未启用，可用管理员 PowerShell 执行：

```powershell
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

然后重启，再执行：

```powershell
wsl --set-default-version 2
```

## 4. 下载并安装 Docker Desktop

官方下载入口：

`https://www.docker.com/products/docker-desktop/`

安装时建议：

- 勾选 `Use WSL 2 instead of Hyper-V`
- 保持默认安装路径即可

安装完成后，启动 Docker Desktop。

首次启动要确认：

1. Docker Desktop 能正常打开
2. 状态显示为 `Engine running`
3. 没有卡在 `Starting`

验证命令：

```powershell
docker --version
docker compose version
docker info
```

如果 `docker compose version` 能输出版本，说明 Compose 插件可用。

## 5. Docker Desktop 推荐设置

打开 Docker Desktop，建议检查这些配置：

1. `Settings` -> `General`
   - 保持 `Use the WSL 2 based engine` 开启

2. `Settings` -> `Resources`
   - 内存建议至少 `6GB`
   - CPU 建议至少 `4`
   - 磁盘空间不足时要提前扩容

3. `Settings` -> `Resources` -> `WSL Integration`
   - 保持默认启用即可

4. `Settings` -> `File sharing`
   - Docker Desktop 新版本基于 WSL2，一般不需要再手动共享磁盘
   - 如果后续出现路径权限问题，再回来看这里

## 6. Windows 终端工具建议

推荐使用：

- PowerShell
- Windows Terminal

如果你更习惯 Linux 风格，也可以进入 WSL2 后执行命令。  
但这份文档默认以 Windows PowerShell 为主。

## 7. 获取项目代码

如果你已经有项目目录，确认仓库根目录里有这些文件：

- `docker-compose.yml`
- `deploy/docker/.env.docker.example`

如果要重新拉项目：

```powershell
cd C:\projects
git clone https://github.com/CAIHEee/jinma_jewelry.git
cd .\jinma_jewelry_system\
```

## 8. 复制 Docker 环境配置模板

在项目根目录执行：

```powershell
Copy-Item .\deploy\docker\.env.docker.example .\.env.docker
```

如果你用的是 Git Bash，也可以：

```bash
cp deploy/docker/.env.docker.example .env.docker
```

## 9. 编辑 `.env.docker`

用 VS Code、Notepad++ 或其他纯文本编辑器打开根目录的 `.env.docker`。

至少要修改这些值：

- `BACKEND_IMAGE`
- `NGINX_IMAGE`
- `MYSQL_ROOT_PASSWORD`
- `AUTH_SECRET_KEY`
- `ROOT_DEFAULT_PASSWORD`
- `APIYI_API_KEY`
- `APP_PUBLIC_BASE_URL`

关键字段解释：

- `NGINX_PORT`
  - Windows 对外访问端口
  - 默认 `80`
  - 如果 80 被占用，可改成 `8080`

- `WEB_CONCURRENCY`
  - Web API 的 gunicorn worker 数量
  - 默认 `2`

- `WORKER_CONCURRENCY`
  - 队列 Worker 进程数量
  - 每个 worker 同一时刻执行 1 个任务
  - 默认模板是 `4`

- `APP_ALLOWED_ORIGINS`
  - CORS 来源
  - 单机调试至少建议保留：
  - `http://localhost,http://127.0.0.1`

- `APP_PUBLIC_BASE_URL`
  - 对外访问地址
  - 例如：
  - `http://192.168.1.50:8080`

建议填写示例：

```env
NGINX_PORT=8080
MYSQL_ROOT_PASSWORD=your-strong-mysql-password
AUTH_SECRET_KEY=your-long-random-secret
ROOT_DEFAULT_PASSWORD=your-root-password
APIYI_API_KEY=your-apiyi-key
APP_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1,http://192.168.1.50:8080
APP_PUBLIC_BASE_URL=http://192.168.1.50:8080
WEB_CONCURRENCY=2
WORKER_CONCURRENCY=4
```

## 10. Windows 上编辑 `.env.docker` 的注意事项

需要特别注意两件事：

1. 文件编码建议使用 `UTF-8`
2. 换行建议使用 `LF` 或 `CRLF` 都可以，但不要混乱

如果你用 VS Code：

- 右下角确认编码是 `UTF-8`
- 行尾最好统一

另外不要在值两边多加引号，除非你明确知道要这么做。

错误示例：

```env
MYSQL_ROOT_PASSWORD="abc123"
```

建议直接写：

```env
MYSQL_ROOT_PASSWORD=abc123
```

## 11. 首次联网部署

进入项目根目录后执行：

```powershell
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

如果你本机还没有对应镜像，这一步会先拉镜像，再启动容器。

这套 Compose 默认会启动：

- `mysql`
- `redis`
- `backend`
- `worker`
- `agent`
- `nginx`

## 12. 查看容器状态

```powershell
docker compose --env-file .env.docker ps
```

看日志：

```powershell
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker logs -f nginx
```

如果想看 agent：

```powershell
docker compose --env-file .env.docker logs -f agent
```

## 13. 验证是否启动成功

健康检查：

```powershell
curl http://127.0.0.1:8080/health
```

如果你把 `NGINX_PORT` 保持成 `80`，就改成：

```powershell
curl http://127.0.0.1/health
```

浏览器访问：

```text
http://127.0.0.1:8080
```

局域网访问：

```text
http://你的局域网IP:8080
```

如果 `NGINX_PORT=80`，则不需要带端口。

## 14. 如何找到本机局域网 IP

PowerShell 执行：

```powershell
ipconfig
```

找到当前网卡下的 IPv4 地址，例如：

```text
192.168.1.50
```

那么局域网访问地址通常就是：

```text
http://192.168.1.50:8080
```

## 15. Windows 防火墙放行端口

如果局域网其他机器打不开页面，通常要放行 `NGINX_PORT`。

图形界面方式：

1. 打开“Windows Defender 防火墙”
2. 进入“高级设置”
3. 选择“入站规则”
4. 新建规则
5. 选择“端口”
6. 填入你的 `NGINX_PORT`
7. 允许连接

如果你用的是 `8080`，就放行 `8080`。

## 16. 容器与数据卷说明

这套 Compose 至少会使用这些持久化卷：

- `jinma_jewelry_system_mysql_data`
- `jinma_jewelry_system_backend_data`
- `jinma_jewelry_system_redis_data`

最重要的是：

- 数据库在 MySQL volume 里
- 本地图片和后端本地数据在 `backend_data` 里
- `backend` 和 `worker` 共用同一个 `backend_data` 卷

不要执行：

```powershell
docker compose down -v
```

这会直接删掉卷，数据库和图片都会丢。

## 17. 停止与重启

停止：

```powershell
docker compose --env-file .env.docker down
```

重新启动：

```powershell
docker compose --env-file .env.docker up -d
```

更新镜像后升级：

```powershell
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

## 18. Windows 机器离线部署

如果目标 Windows 机器不能联网，推荐使用项目已经支持的离线包方式。

开发机先生成离线包：

```bash
deploy/docker/prepare_offline_bundle.sh
```

生成后的目录在：

```text
dist/offline_bundle
```

里面通常包含：

- `jinma-images.tar`
- `.env.docker`
- `docker-compose.yml`
- `start_offline_stack.sh`
- `stop_offline_stack.sh`
- `README.md`

注意：

- 这些脚本默认是 Linux shell 脚本
- 如果目标是纯 Windows 环境，建议在 WSL2 里执行离线包脚本
- 或者把离线包镜像导入后，用 PowerShell 手动执行 `docker compose`
- 备份和恢复脚本会优先读取脚本所在目录下的 `.env.docker` 和 `docker-compose.yml`，不会强依赖仓库里的固定路径

Windows 下的可行离线流程是：

1. 把 `dist/offline_bundle` 整个目录拷到 Windows 机器
2. 安装 Docker Desktop
3. 打开 PowerShell，进入离线包目录
4. 导入镜像：

```powershell
docker load -i .\jinma-images.tar
```

5. 启动服务：

```powershell
docker compose --env-file .env.docker -f docker-compose.yml up -d
```

6. 查看状态：

```powershell
docker compose --env-file .env.docker -f docker-compose.yml ps
```

如果你是在 WSL2 里操作，也可以直接按 [deploy/docker/README.offline.md](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/README.offline.md:1) 的 shell 方式执行。

## 19. 备份与恢复

如果你是在 Linux / WSL2 环境里运行项目自带脚本，可参考：

- [README.md](/home/chaihe/projects/jinma_jewelry_system/README.md:515)
- [deploy/docker/README.offline.md](/home/chaihe/projects/jinma_jewelry_system/deploy/docker/README.offline.md:131)

如果是纯 Windows PowerShell，不直接跑这些 `.sh` 脚本，建议至少先做两类备份：

1. `.env.docker`
2. Docker 卷中的数据库和图片数据

如果后续需要，我可以再补一份“Windows PowerShell 版备份与恢复指南”。

## 20. 常见问题排查

### Docker Desktop 一直启动失败

优先检查：

- BIOS 虚拟化是否开启
- WSL2 是否安装成功
- Windows 版本是否过旧

### `docker compose` 命令不可用

执行：

```powershell
docker compose version
```

如果报错，通常是 Docker Desktop 没装好，或者引擎未启动。

### 80 端口被占用

改 `.env.docker`：

```env
NGINX_PORT=8080
```

然后重启：

```powershell
docker compose --env-file .env.docker up -d
```

### 页面能打开，但生图任务不执行

优先看：

```powershell
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker logs -f backend
```

再检查：

- `APIYI_API_KEY` 是否已填写
- Redis / MySQL 是否健康
- `WORKER_CONCURRENCY` 是否过低

### 一个用户提交后，另一个用户一直排队

先看这几个变量：

- `WORKER_CONCURRENCY`
- `QUEUE_USER_MAX_ACTIVE_JOBS`
- `QUEUE_ROOT_MAX_ACTIVE_JOBS`

含义：

- `WORKER_CONCURRENCY` 控制同时跑几个队列 worker
- `QUEUE_USER_MAX_ACTIVE_JOBS` 控制普通用户同时允许几个活跃任务
- `QUEUE_ROOT_MAX_ACTIVE_JOBS` 控制 root 用户同时允许几个活跃任务

### 改了 `.env.docker` 不生效

重新启动容器：

```powershell
docker compose --env-file .env.docker up -d
```

如果仍不生效，再看是不是改错了文件。运行时要改的是根目录的：

```text
.env.docker
```

不是模板文件：

```text
deploy/docker/.env.docker.example
```

### 图片或数据库突然没了

大概率执行过：

```powershell
docker compose down -v
```

或者手动删过 volume。

## 21. 推荐的 Windows 部署顺序

1. 开启 BIOS 虚拟化
2. 安装并验证 WSL2
3. 安装 Docker Desktop
4. 把项目放到 `C:\projects` 或 `D:\projects`
5. 复制 `deploy/docker/.env.docker.example` 为根目录 `.env.docker`
6. 填好 `.env.docker`
7. 执行 `docker compose --env-file .env.docker pull`
8. 执行 `docker compose --env-file .env.docker up -d`
9. 用 `docker compose ps` 和 `/health` 验证
10. 放行 Windows 防火墙端口，供局域网访问

## 22. 最简命令清单

首次部署：

```powershell
cd C:\projects\jinma_jewelry_system
Copy-Item .\deploy\docker\.env.docker.example .\.env.docker
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker ps
```

看日志：

```powershell
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f worker
```

更新：

```powershell
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

停止：

```powershell
docker compose --env-file .env.docker down
```
