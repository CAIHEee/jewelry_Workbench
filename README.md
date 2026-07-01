# 珠宝 AI 生图系统

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

推荐使用你本机已有的 `rasa` conda 环境。

```powershell
cd E:\A_GS_project\文生图需求\flux_system\backend
conda activate rasa
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

说明：

- `--host 0.0.0.0` 表示允许局域网其他设备访问后端。
- 如果只写 `uvicorn app.main:app --reload`，默认会监听 `127.0.0.1:8000`，只能本机访问。
- 前端开发服务器现在会把 `/api` 请求代理到 `http://127.0.0.1:8000`，所以前后端在同一台机器上运行时也能正常工作。

## 前端启动

```powershell
cd E:\A_GS_project\文生图需求\flux_system\frontend
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

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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

```powershell
cd E:\A_GS_project\文生图需求\flux_system\backend
conda activate rasa
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

然后重启前端：

```powershell
cd E:\A_GS_project\文生图需求\flux_system\frontend
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

## 常用接口

```text
GET  /health
GET  /api/v1/system/summary
GET  /api/v1/ai/models
GET  /api/v1/history
GET  /api/v1/assets
POST /api/v1/ai/text-to-image
POST /api/v1/ai/fuse-images
POST /api/v1/ai/reference-image-transform
POST /api/v1/ai/multi-view
POST /api/v1/ai/split-multi-view
```

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
2. 启动后端。
3. 访问 `http://127.0.0.1:8000/health` 确认后端正常。
4. 启动前端。
5. 访问 `http://192.168.10.243:5173` 进行局域网调试。

## 当前主要功能

- 文生图
- 多图融合
- 生成多视图
- 多视图切图
- 线稿转写实图
- 转灰度图
- 历史记录
- 资产管理

