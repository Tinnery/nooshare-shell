import logging

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
