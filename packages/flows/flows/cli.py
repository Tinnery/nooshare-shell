import logging
from pathlib import Path
from typing import Optional

import typer

from flows.config import LOG_LEVEL

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer(help="flows command line application")


@app.command()
def run(name: str = "system") -> None:
    logger.info("Application started")
    typer.echo(f"Hello {name}")


@app.command()
def daily_etl(
    work_dir: Optional[Path] = typer.Option(None, help="Working directory for temporary files"),
    output_dir: Optional[Path] = typer.Option(None, help="Output directory for CSV files"),
    max_workers: int = typer.Option(8, help="Number of parallel workers for parsing"),
) -> None:
    """Run daily data ETL: S3 -> Parse -> PostgreSQL."""
    from flows.etl import run_daily_etl
    
    logger.info("Starting daily ETL flow...")
    result = run_daily_etl(
        work_dir=work_dir,
        output_dir=output_dir,
        max_workers=max_workers,
    )
    typer.echo(f"ETL completed: {result['symbols']} symbols, {result['rows_loaded']:,} rows loaded")
