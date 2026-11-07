# Dagster 使用指南

本指南介绍如何使用 Dagster 调度 JQ ETL 流程（从 RustFS 读取数据并写入 PostgreSQL）。

## 目录结构

```
nooshare-shell/
├── dagster_defs/
│   ├── __init__.py       # Dagster 定义入口（assets, jobs, schedules）
│   ├── assets.py         # Asset 定义（JQ ETL flow）
│   └── config.py         # 配置 schema
├── workspace.yaml        # Dagster workspace 配置
└── flows/
    └── jq/               # 实际 ETL 逻辑
```

## 安装依赖

```bash
# 在 WSL 中
cd /home/travis/tintin/nooshare-shell

# 安装项目（包含 dagster）
pip install -e ".[all]"
```

## 设置环境变量

创建 `.env` 文件（**不要提交到 git**）：

```bash
# S3/RustFS 凭证
export NOOSHARE_S3_ENDPOINT="http://your-rustfs-host:9000"
export NOOSHARE_S3_ACCESS_KEY="your-access-key"
export NOOSHARE_S3_SECRET_KEY="your-secret-key"
export NOOSHARE_S3_BUCKET="tdx-data"

# PostgreSQL 凭证
export NOOSHARE_PG_HOST="localhost"
export NOOSHARE_PG_PORT="5432"
export NOOSHARE_PG_USER="nooshare"
export NOOSHARE_PG_PASSWORD="your-password"
export NOOSHARE_PG_DATABASE="nooshare_test"
```

加载环境变量：

```bash
source .env
```

## 启动 Dagster Web Server

```bash
# 方式 1：使用 dagster dev（推荐，开发模式）
dagster dev

# 方式 2：指定 workspace
dagster-webserver -w workspace.yaml
```

默认访问地址：http://localhost:3000

## 使用方式

### 1. 手动触发 Materialize

在 Dagster Web UI 中：

1. 打开 http://localhost:3000
2. 进入 **Assets** 页面
3. 找到 `jq_rustfs_to_pg` asset
4. 点击 **Materialize** 按钮
5. 查看运行日志和结果

### 2. 通过 CLI 触发

```bash
# 触发单次运行
dagster job execute -j jq_etl_job

# 查看 job 状态
dagster job list
```

### 3. 自动调度

已配置 cron schedule：每天凌晨 2:00 自动运行

```python
# dagster_defs/__init__.py
jq_etl_schedule = ScheduleDefinition(
    name="jq_etl_schedule",
    job=jq_etl_job,
    cron_schedule="0 2 * * *",  # 每天 02:00
)
```

修改调度时间：编辑 `dagster_defs/__init__.py` 中的 `cron_schedule`

常用 cron 表达式：
- `"0 2 * * *"` — 每天 2:00 AM
- `"0 */6 * * *"` — 每 6 小时
- `"0 2 * * 1-5"` — 工作日 2:00 AM
- `"0 2 1 * *"` — 每月 1 号 2:00 AM

## 监控和日志

### 查看运行历史

在 Web UI 的 **Runs** 页面可以看到所有历史运行记录。

### 查看 Asset 元数据

每次运行后，asset 会记录：
- 写入的表数量
- 总行数
- 每张表的详细行数
- 数据库和 schema 信息

### 失败重试

如果运行失败：
1. 在 **Runs** 页面找到失败的运行
2. 点击 **Re-execute** 重试
3. 查看日志定位问题

## 进阶配置

### 多环境配置

创建不同的 workspace 文件：

```yaml
# workspace-dev.yaml
load_from:
  - python_module:
      module_name: dagster_defs
      attribute: defs
      working_directory: .

# workspace-prod.yaml
load_from:
  - python_module:
      module_name: dagster_defs
      attribute: defs
      working_directory: .
```

### 添加 Resources

如果需要数据库连接池等资源：

```python
# dagster_defs/__init__.py
from dagster import resource

@resource
def postgres_resource(init_context):
    import psycopg2
    return psycopg2.connect(
        host=os.environ["NOOSHARE_PG_HOST"],
        port=os.environ["NOOSHARE_PG_PORT"],
        user=os.environ["NOOSHARE_PG_USER"],
        password=os.environ["NOOSHARE_PG_PASSWORD"],
        database=os.environ["NOOSHARE_PG_DATABASE"],
    )

defs = Definitions(
    assets=all_assets,
    jobs=[jq_etl_job],
    schedules=[jq_etl_schedule],
    resources={
        "postgres": postgres_resource,
    },
)
```

### 添加 Sensors

除了 cron schedule，还可以用 sensor 监听外部事件：

```python
from dagster import sensor, RunRequest

@sensor(job=jq_etl_job)
def rustfs_data_available_sensor(context):
    """当 RustFS 有新数据时触发"""
    # 检查逻辑...
    if new_data_available():
        yield RunRequest(run_key="rustfs-update")
```

## 故障排查

### 问题：环境变量未加载

```bash
# 检查环境变量
echo $NOOSHARE_S3_ENDPOINT

# 重新加载
source .env
```

### 问题：Dagster Web Server 启动失败

```bash
# 检查端口占用
lsof -i :3000

# 使用其他端口
dagster dev --port 3001
```

### 问题：Asset materialization 失败

1. 查看 Dagster Web UI 中的日志
2. 检查环境变量是否正确
3. 验证 S3 和 PostgreSQL 连接
4. 手动运行测试：

```bash
python scripts/jq/flow_rustfs_to_pg.py
```

## 生产部署

### 使用 Dagster Cloud

```bash
# 安装 Dagster Cloud CLI
pip install dagster-cloud

# 部署
dagster-cloud deploy
```

### 使用 Docker

创建 `Dockerfile`：

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . .

RUN pip install -e ".[all]"

EXPOSE 3000
CMD ["dagster-webserver", "-w", "workspace.yaml", "--host", "0.0.0.0"]
```

构建和运行：

```bash
docker build -t nooshare-dagster .
docker run -p 3000:3000 --env-file .env nooshare-dagster
```

## 参考资源

- [Dagster 官方文档](https://docs.dagster.io/)
- [Dagster Assets 指南](https://docs.dagster.io/concepts/assets)
- [Dagster Schedules](https://docs.dagster.io/concepts/schedules-sensors)
