# Jinma Offline Bundle

这是离线部署包说明。目标机器只需要安装 Docker 与 Docker Compose。

构建离线包，开发机运行：bash deploy/docker/prepare_offline_bundle.sh

## 包内文件

- `jinma-images.tar`
- `.env.docker`
- `docker-compose.yml`
- `start_offline_stack.sh`
- `stop_offline_stack.sh`

## 首次启动

进入离线包目录：

```bash
cd offline_bundle
chmod +x start_offline_stack.sh
./start_offline_stack.sh
```

这一步会自动：

- 导入镜像
- 启动 `mysql`
- 启动 `redis`
- 启动 `backend`
- 启动 `worker`，默认按 `.env.docker` 里的 `WORKER_CONCURRENCY=2` 开启 2 个队列 Worker 进程
- 启动 `nginx`

## 查看状态

```bash
docker compose --env-file .env.docker -f docker-compose.yml ps
docker compose --env-file .env.docker -f docker-compose.yml logs -f backend
docker compose --env-file .env.docker -f docker-compose.yml logs -f worker
```

## 访问地址

默认访问：

```text
http://127.0.0.1:8080
```

如果你修改了 `.env.docker` 里的 `NGINX_PORT`，按对应端口访问。

健康检查：

```bash
curl http://127.0.0.1:8080/health
```

## 停止服务

```bash
chmod +x stop_offline_stack.sh
./stop_offline_stack.sh
```

## 更新离线包

如果你拿到了新的离线包：

1. 先在旧目录停止：

```bash
./stop_offline_stack.sh
```

2. 再到新目录启动：

```bash
./start_offline_stack.sh
```

Compose 项目名固定为 `jinma_jewelry_system`，会继续复用原有数据卷。

### 更新是否会覆盖旧数据

正常更新不会覆盖旧数据。

下面这些操作只会更新程序，不会删除已有数据库和图片数据：

```bash
./stop_offline_stack.sh
./start_offline_stack.sh
```

或者重新生成新的离线包后，在新目录执行：

```bash
./stop_offline_stack.sh
./start_offline_stack.sh
```

只要 Docker 数据卷还在，系统就会继续使用旧数据。

会导致数据丢失或被旧备份覆盖的情况只有这些：

- 执行了 `docker compose down -v`
- 手动删除了 Docker volume
- 执行了 `restore_offline_stack.sh`，并恢复了旧备份
- 修改了 Compose 项目名或卷名，导致挂到了另一套新卷

推荐的安全更新顺序：

1. 先备份当前数据
2. 停止旧服务
3. 使用新的离线包启动
4. 检查页面和接口是否正常

## 数据位置

- MySQL 数据卷：`jinma_jewelry_system_mysql_data`
- 本地图片卷：`jinma_jewelry_system_backend_data`
- Redis 数据卷：`jinma_jewelry_system_redis_data`

不要执行：

```bash
docker compose down -v
```

这会删除数据卷。

## 备份与恢复

备份当前数据库和本地图片：

```bash
chmod +x backup_offline_stack.sh
./backup_offline_stack.sh
```

默认会在离线包目录的同级目录下生成：

- `offline_backup/jinma.sql`
- `offline_backup/backend_data.tar.gz`

恢复数据库和本地图片：

```bash
chmod +x restore_offline_stack.sh
./restore_offline_stack.sh
```

如果交付给新机器时需要同时迁移旧数据，除了离线包目录，还应一起提供：

- `jinma.sql`
- `backend_data.tar.gz`

如果你的离线包目录不在 `dist/offline_bundle`，而是已经整包拷贝到别的机器，脚本会优先读取脚本所在目录下的 `.env.docker` 和 `docker-compose.yml`。
