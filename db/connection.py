"""Database connection and query execution engine."""
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

from config.settings import settings

logger = logging.getLogger(__name__)

class DatabaseEngine:
    def __init__(self, read_only: bool = False):
        self.read_only = read_only
        self._duckdb_conn = None
        self._is_duckdb = settings.USE_DUCKDB_FALLBACK

    def _get_postgres_connection(self):
        try:
            import psycopg2
            import psycopg2.extras
            url = settings.get_database_url(read_only=self.read_only)
            conn = psycopg2.connect(
                url,
                connect_timeout=3,
            )
            return conn
        except Exception as e:
            logger.debug(f"PostgreSQL connection failed ({e}), attempting local embedded backend.")
            return None

    def _get_duckdb_connection(self):
        if self._duckdb_conn is None:
            import duckdb
            settings.DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._duckdb_conn = duckdb.connect(str(settings.DUCKDB_PATH), read_only=self.read_only)
            # Enable standard postgres compatibility extensions/settings
            try:
                self._duckdb_conn.execute("SET preserve_insertion_order=false;")
            except Exception:
                pass
        return self._duckdb_conn

    def is_postgres_available(self) -> bool:
        conn = self._get_postgres_connection()
        if conn:
            conn.close()
            return True
        return False

    def execute_query(
        self,
        query: str,
        params: Optional[Union[List[Any], Tuple[Any, ...], Dict[str, Any]]] = None,
        timeout_seconds: int = 10,
    ) -> pd.DataFrame:
        """Executes a SQL query and returns results as a pandas DataFrame."""
        pg_conn = None if self._is_duckdb else self._get_postgres_connection()

        if pg_conn:
            try:
                with pg_conn.cursor() as cursor:
                    if timeout_seconds:
                        cursor.execute(f"SET statement_timeout = {int(timeout_seconds * 1000)};")
                    cursor.execute(query, params or ())
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                        rows = cursor.fetchall()
                        return pd.DataFrame(rows, columns=columns)
                    pg_conn.commit()
                    return pd.DataFrame()
            finally:
                pg_conn.close()
        else:
            # Embedded DuckDB execution
            conn = self._get_duckdb_connection()
            # DuckDB statement timeout or query execution
            if params:
                return conn.execute(query, params).df()
            else:
                return conn.execute(query).df()

    def execute_raw_sql(self, sql_script: str) -> None:
        """Executes DDL or migration scripts."""
        pg_conn = None if self._is_duckdb else self._get_postgres_connection()

        if pg_conn:
            try:
                pg_conn.autocommit = True
                with pg_conn.cursor() as cursor:
                    cursor.execute(sql_script)
            finally:
                pg_conn.close()
        else:
            conn = self._get_duckdb_connection()
            # Clean up PostgreSQL specific DDL elements for DuckDB compatibility if needed
            cleaned_sql = sql_script.replace('TIMESTAMP WITH TIME ZONE', 'TIMESTAMP')
            cleaned_sql = cleaned_sql.replace('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";', '')
            cleaned_sql = cleaned_sql.replace('JSONB', 'JSON')
            cleaned_sql = cleaned_sql.replace('NUMERIC(10, 2)', 'DOUBLE')
            cleaned_sql = cleaned_sql.replace('NUMERIC(12, 2)', 'DOUBLE')
            # Split statements
            statements = [s.strip() for s in cleaned_sql.split(';') if s.strip()]
            for stmt in statements:
                # Skip role/grant statements in DuckDB
                if any(kw in stmt.upper() for kw in ['CREATE ROLE', 'GRANT ', 'REVOKE ', 'COMMENT ON ']):
                    continue
                try:
                    conn.execute(stmt)
                except Exception as e:
                    logger.debug(f"DuckDB raw SQL note on statement: {stmt[:50]}... -> {e}")

    def close(self):
        if self._duckdb_conn:
            try:
                self._duckdb_conn.close()
            except Exception:
                pass
            self._duckdb_conn = None


def get_db(read_only: bool = False) -> DatabaseEngine:
    return DatabaseEngine(read_only=read_only)
