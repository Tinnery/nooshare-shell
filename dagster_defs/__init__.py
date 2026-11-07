"""Dagster definitions for nooshare-shell flows."""

from dagster import Definitions, EnvVar, ScheduleDefinition, define_asset_job
from .assets import jq_rustfs_to_pg_asset

# Define the job
jq_etl_job = define_asset_job(
    name="jq_etl_job",
    description="Download JQ data from RustFS and write to PostgreSQL",
)

# Define the schedule (runs daily at 2 AM)
jq_etl_schedule = ScheduleDefinition(
    name="jq_etl_schedule",
    job=jq_etl_job,
    cron_schedule="0 2 * * *",  # Daily at 2:00 AM
)

# Define all assets
all_assets = [jq_rustfs_to_pg_asset]

# Create the Definitions object
defs = Definitions(
    assets=all_assets,
    jobs=[jq_etl_job],
    schedules=[jq_etl_schedule],
    resources={
        # Resources can be configured here or via environment variables
    },
)
