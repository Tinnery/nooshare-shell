# NooShare Dashboard

知识库浏览器和运维仪表盘。

## 架构

- **后端**: FastAPI + Uvicorn
- **前端**: Astro (static output) + Tailwind CSS 4
- **部署**: 单一 ASGI 入口，托管前端静态文件

## 目录结构

```
dashboard/
├── dashboard/          # Python 后端
│   ├── app.py         # FastAPI 应用和 API 路由
│   ├── server.py      # Uvicorn 入口
│   ├── config.py      # 配置
│   └── knowledge.py   # 知识库读取和渲染
├── frontend/          # Astro 前端
│   ├── src/
│   │   ├── layouts/   # 布局组件
│   │   ├── pages/     # 页面
│   │   ├── components/# 可复用组件
│   │   └── styles/    # 全局样式
│   └── dist/          # 构建输出（自动生成）
└── tests/             # 测试
```

## 开发

### 安装依赖

```bash
# Python 依赖
uv sync

# 前端依赖
cd frontend
npm install
```

### 运行开发服务器

```bash
# 终端 1: 启动后端
uv run dashboard --reload

# 终端 2: 启动前端（自动代理 /api 到后端）
cd frontend
npm run dev
```

前端开发服务器运行在 `http://localhost:4321`，API 请求自动代理到后端 `http://localhost:8000`。

### 构建生产版本

```bash
cd frontend
npm run build
```

构建产物输出到 `frontend/dist/`，后端会自动托管这些静态文件。

### 运行生产服务器

```bash
uv run dashboard --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000`。

## API

- `GET /api/health` - 健康检查
- `GET /api/knowledge/tree` - 获取知识库目录树
- `GET /api/knowledge/content/{path}` - 获取文件内容（Markdown 渲染为 HTML）
- `GET /api/knowledge/meta/{path}` - 获取文件元数据

## 配置

编辑 `dashboard/config.py` 修改：

- `HOST` / `PORT` - 服务器绑定地址
- `KNOWLEDGE_DIR` - 知识库目录路径
- `SUPPORTED_EXTENSIONS` - 支持的文件扩展名
- `MAX_FILE_SIZE` - 最大文件大小

## 特性

- 自动扫描知识库目录
- Markdown 文件支持 frontmatter 解析
- Markdown 渲染为 HTML
- 响应式侧边栏目录树
- 暗色模式支持（占位）
- 统计卡片（文档数量、目录数量、API 状态）
- 快速访问列表
