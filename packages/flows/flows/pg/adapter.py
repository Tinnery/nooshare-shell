"""PostgreSQL adapter implementation."""

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg2

logger = logging.getLogger(__name__)


@dataclass
class PGConfig:
    """PostgreSQL connection configuration."""
    
    dbname: str
    user: str = "travis"
    host: Optional[str] = None
    port: Optional[int] = None
    password: Optional[str] = None
    connect_timeout: int = 10
    application_name: str = "flows-etl"
    
    @classmethod
    def from_env(cls) -> "PGConfig":
        """Load configuration from environment variables."""
        import os
        return cls(
            dbname=os.getenv("FLOWS_PG_DATABASE") or os.getenv("PGDATABASE", "nooshare_test"),
            user=os.getenv("FLOWS_PG_USER", os.getenv("PGUSER", "travis")),
            host=os.getenv("FLOWS_PG_HOST", os.getenv("PGHOST")),
            port=int(os.getenv("FLOWS_PG_PORT", os.getenv("PGPORT", "5432"))) if os.getenv("FLOWS_PG_PORT") or os.getenv("PGPORT") else None,
            password=os.getenv("FLOWS_PG_PASSWORD", os.getenv("PGPASSWORD")),
            connect_timeout=int(os.getenv("FLOWS_PG_CONNECT_TIMEOUT", "10")),
        )
    
    def to_dict(self) -> dict:
        """Convert to psycopg2 connection parameters."""
        params = {
            "dbname": self.dbname,
            "user": self.user,
            "connect_timeout": self.connect_timeout,
            "application_name": self.application_name,
        }
        if self.host:
            params["host"] = self.host
        if self.port:
            params["port"] = self.port
        if self.password:
            params["password"] = self.password
        return params


class PGAdapter:
    """Adapter for PostgreSQL operations."""
    
    def __init__(self, config: PGConfig):
        self.config = config
        self._conn = None
    
    def connect(self):
        """Establish database connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**self.config.to_dict())
            logger.info(f"Connected to PostgreSQL: {self.config.dbname}")
        return self._conn
    
    def close(self):
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.info("PostgreSQL connection closed")
    
    def create_daily_table(self) -> None:
        """Create daily price table if not exists."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS tdx")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tdx.daily (
                    symbol TEXT NOT NULL,
                    date INTEGER NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume BIGINT NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.commit()
            logger.info("Table tdx.daily ready")
    
    def load_daily_data(self, daily_dir: Path) -> int:
        """Load daily TSV data into PostgreSQL.
        
        Args:
            daily_dir: Directory containing daily TSV files (tab-separated, with header)
            
        Returns:
            Number of rows loaded
        """
        conn = self.connect()
        total = 0
        
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE tdx.daily")
            
            for tsv_file in sorted(daily_dir.glob("*.tsv")):
                symbol = tsv_file.stem
                rows = []
                seen = set()
                
                with open(tsv_file, "r") as f:
                    header = f.readline()  # skip header: date\topen\thigh\tlow\tclose\tvolume\tamount
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        parts = line.split("\t")
                        if len(parts) < 7:
                            continue
                        date_str = parts[0]
                        o, h, l, c = parts[1], parts[2], parts[3], parts[4]
                        v, a = parts[5], parts[6]
                        key = (symbol, int(date_str))
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append((
                            symbol,
                            key[1],
                            float(o),
                            float(h),
                            float(l),
                            float(c),
                            int(float(v)),
                            float(a),
                        ))
                
                if rows:
                    copy_sql = "COPY tdx.daily (symbol, date, open, high, low, close, volume, amount) FROM STDIN WITH (FORMAT csv)"
                    csv_buffer = io.StringIO()
                    for r in rows:
                        csv_buffer.write(f"{r[0]},{r[1]},{r[2]:.2f},{r[3]:.2f},{r[4]:.2f},{r[5]:.2f},{r[6]},{r[7]:.2f}\n")
                    csv_buffer.seek(0)
                    cur.copy_expert(copy_sql, csv_buffer)
                    total += len(rows)
            
            conn.commit()
        
        logger.info(f"Loaded {total:,} daily rows")
        return total
