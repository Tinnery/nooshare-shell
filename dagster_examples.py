"""
Dagster 使用示例

这个文件展示了如何在 Dagster 中使用 JQ ETL flow。
"""

# ============================================================================
# 示例 1: 基本的 Asset 使用
# ============================================================================

from dagster import asset, Definitions
from flows.jq import run_rustfs_to_pg
import os


@asset
def jq_data_from_rustfs():
    """从 RustFS 下载 JQ 数据并写入 PostgreSQL"""
    
    loaded = run_rustfs_to_pg(
        endpoint_url=os.environ["NOOSHARE_S3_ENDPOINT"],
        access_key_id=os.environ["NOOSHARE_S3_ACCESS_KEY"],
        secret_access_key=os.environ["NOOSHARE_S3_SECRET_KEY"],
        bucket="tdx-data",
        object_key="meta1",
        database="nooshare_test",
        schema="jq",
        verbose=True,
    )
    
    return f"Loaded {sum(loaded.values())} rows into {len(loaded)} tables"


# ============================================================================
# 示例 2: 带配置的 Asset
# ============================================================================

from dagster import Config


class JqEtlConfig(Config):
    """JQ ETL 配置"""
    bucket: str = "tdx-data"
    object_key: str = "meta1"
    database: str = "nooshare_test"
    schema: str = "jq"


@asset
def jq_etl_with_config(context, config: JqEtlConfig):
    """使用配置的 JQ ETL"""
    
    context.log.info(f"Processing {config.bucket}/{config.object_key}")
    
    loaded = run_rustfs_to_pg(
        endpoint_url=os.environ["NOOSHARE_S3_ENDPOINT"],
        access_key_id=os.environ["NOOSHARE_S3_ACCESS_KEY"],
        secret_access_key=os.environ["NOOSHARE_S3_SECRET_KEY"],
        bucket=config.bucket,
        object_key=config.object_key,
        database=config.database,
        schema=config.schema,
        verbose=True,
    )
    
    context.log.info(f"Loaded {loaded}")
    return loaded


# ============================================================================
# 示例 3: 带依赖的 Asset 链
# ============================================================================

@asset
def raw_jq_data():
    """第一步：从 RustFS 下载原始数据"""
    # 下载逻辑...
    return {"status": "downloaded", "file": "meta1"}


@asset
def decoded_jq_data(raw_jq_data):
    """第二步：解码数据（依赖第一步）"""
    # 解码逻辑...
    return {"status": "decoded", "rows": 1000}


@asset
def jq_data_in_postgres(decoded_jq_data):
    """第三步：写入 PostgreSQL（依赖第二步）"""
    # 写入逻辑...
    return {"status": "loaded", "tables": 7}


# ============================================================================
# 示例 4: 使用 Resources
# ============================================================================

from dagster import resource, InitResourceContext
import psycopg2


class PostgresResource:
    """PostgreSQL 连接资源"""
    
    def __init__(self, host, port, user, password, database):
        self.connection_params = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
        }
        self.conn = None
    
    def connect(self):
        """建立连接"""
        self.conn = psycopg2.connect(**self.connection_params)
        return self.conn
    
    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()


@resource(config_schema={
    "host": str,
    "port": int,
    "user": str,
    "password": str,
    "database": str,
})
def postgres_resource(init_context: InitResourceContext):
    """PostgreSQL 资源工厂"""
    resource = PostgresResource(
        host=init_context.resource_config["host"],
        port=init_context.resource_config["port"],
        user=init_context.resource_config["user"],
        password=init_context.resource_config["password"],
        database=init_context.resource_config["database"],
    )
    
    try:
        yield resource
    finally:
        resource.close()


@asset(required_resource_keys={"postgres"})
def jq_data_with_resource(context):
    """使用 PostgreSQL 资源的 Asset"""
    
    pg = context.resources.postgres
    conn = pg.connect()
    
    # 执行 ETL...
    loaded = run_rustfs_to_pg(
        endpoint_url=os.environ["NOOSHARE_S3_ENDPOINT"],
        access_key_id=os.environ["NOOSHARE_S3_ACCESS_KEY"],
        secret_access_key=os.environ["NOOSHARE_S3_SECRET_KEY"],
        bucket="tdx-data",
        object_key="meta1",
        database="nooshare_test",
        schema="jq",
        verbose=True,
    )
    
    # 验证数据
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jq.stocks")
        count = cur.fetchone()[0]
    
    context.log.info(f"Verified: {count} rows in jq.stocks")
    return loaded


# ============================================================================
# 示例 5: 自定义 Schedule
# ============================================================================

from dagster import schedule, RunRequest


@schedule(
    job=jq_etl_job,
    cron_schedule="0 2 * * 1-5",  # 工作日凌晨 2 点
    execution_timezone="Asia/Shanghai",
)
def weekday_jq_etl_schedule(context):
    """工作日调度"""
    
    # 可以添加条件判断是否执行
    if should_run_today():
        yield RunRequest(
            run_key=f"jq-etl-{context.scheduled_execution_time.date()}",
            config={
                "ops": {
                    "jq_rustfs_to_pg_asset": {
                        "config": {
                            "bucket": "tdx-data",
                            "object_key": "meta1",
                        }
                    }
                }
            },
        )


# ============================================================================
# 示例 6: Sensor（事件驱动）
# ============================================================================

from dagster import sensor, RunRequest


@sensor(job=jq_etl_job)
def rustfs_data_sensor(context):
    """监听 RustFS 新数据"""
    
    # 检查是否有新数据
    last_event = context.cursor or {"last_check": None}
    
    # 这里添加检查逻辑
    if new_data_available():
        yield RunRequest(
            run_key=f"rustfs-update-{datetime.now().timestamp()}",
        )
        
        # 更新 cursor
        context.update_cursor({"last_check": datetime.now().isoformat()})


# ============================================================================
# 示例 7: Partitioned Asset（分区）
# ============================================================================

from dagster import DailyPartitionsDefinition, asset


partitions_def = DailyPartitionsDefinition(start_date="2026-01-01")


@asset(partitions_def=partitions_def)
def partitioned_jq_data(context):
    """按日期分区的 JQ 数据"""
    
    partition_key = context.partition_key  # 例如 "2026-01-15"
    
    # 处理特定日期的数据
    context.log.info(f"Processing partition: {partition_key}")
    
    loaded = run_rustfs_to_pg(
        endpoint_url=os.environ["NOOSHARE_S3_ENDPOINT"],
        access_key_id=os.environ["NOOSHARE_S3_ACCESS_KEY"],
        secret_access_key=os.environ["NOOSHARE_S3_SECRET_KEY"],
        bucket="tdx-data",
        object_key=f"meta1-{partition_key}",  # 按日期分区的 object key
        database="nooshare_test",
        schema="jq",
        verbose=True,
    )
    
    return loaded


# ============================================================================
# 示例 8: 完整的 Definitions
# ============================================================================

from dagster import Definitions, ScheduleDefinition


# 定义所有 assets
all_assets = [
    jq_data_from_rustfs,
    jq_etl_with_config,
    raw_jq_data,
    decoded_jq_data,
    jq_data_in_postgres,
]

# 定义 jobs
from dagster import define_asset_job

jq_etl_job = define_asset_job(
    name="jq_etl_job",
    description="JQ ETL pipeline",
)

# 定义 schedules
daily_schedule = ScheduleDefinition(
    name="daily_jq_etl",
    job=jq_etl_job,
    cron_schedule="0 2 * * *",
)

# 组合所有定义
defs = Definitions(
    assets=all_assets,
    jobs=[jq_etl_job],
    schedules=[daily_schedule],
    resources={
        "postgres": postgres_resource.configured({
            "host": os.environ.get("NOOSHARE_PG_HOST", "localhost"),
            "port": int(os.environ.get("NOOSHARE_PG_PORT", "5432")),
            "user": os.environ.get("NOOSHARE_PG_USER", "nooshare"),
            "password": os.environ.get("NOOSHARE_PG_PASSWORD", ""),
            "database": os.environ.get("NOOSHARE_PG_DATABASE", "nooshare_test"),
        }),
    },
)
