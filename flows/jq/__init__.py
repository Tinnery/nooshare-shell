"""JQ data flow: RustFS → decode → PostgreSQL."""

from .rustfs_to_pg import run_rustfs_to_pg

__all__ = ["run_rustfs_to_pg"]
