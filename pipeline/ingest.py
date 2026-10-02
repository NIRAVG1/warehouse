"""Pharma Weekly Sales Ingestion & Upsert Engine with Data Quality Guardrails."""
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import pandas as pd

from config.settings import settings
from db.connection import get_db
from pipeline.alerts import AlertDispatcher
from pipeline.dq_checks import DataQualityChecker, DQReport

logger = logging.getLogger(__name__)


@dataclass
class PipelineRunResult:
    run_id: str
    file_name: str
    week_number: int
    status: str  # 'SUCCESS', 'CRITICAL_FAILURE', 'LOADED_WITH_WARNINGS'
    rows_staged: int
    rows_loaded: int
    dq_critical_count: int
    dq_warning_count: int
    execution_time_ms: float
    report: DQReport
    details: Dict[str, Any]


class SalesIngestionPipeline:
    def __init__(
        self,
        valid_territories: Optional[Set[str]] = None,
        valid_products: Optional[Set[str]] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
    ):
        self.db = get_db(read_only=False)
        self.alerts = alert_dispatcher or AlertDispatcher()
        self.valid_territories = valid_territories or self._load_valid_territories()
        self.valid_products = valid_products or self._load_valid_products()
        self.dq_checker = DataQualityChecker(
            valid_territories=self.valid_territories,
            valid_products=self.valid_products,
        )
        self._historical_row_counts: List[int] = []

    def _load_valid_territories(self) -> Set[str]:
        try:
            df = self.db.execute_query("SELECT territory_id FROM dim_territory")
            if not df.empty:
                return set(df["territory_id"].astype(str))
        except Exception:
            pass
        # Fallback from dimensions csv
        csv_path = settings.DATA_DIR / "dimensions" / "territories.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            return set(df["territory_id"].astype(str))
        return set()

    def _load_valid_products(self) -> Set[str]:
        try:
            df = self.db.execute_query("SELECT product_id FROM dim_product")
            if not df.empty:
                return set(df["product_id"].astype(str))
        except Exception:
            pass
        csv_path = settings.DATA_DIR / "dimensions" / "products.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            return set(df["product_id"].astype(str))
        return set()

    def ingest_weekly_file(
        self,
        file_path: Path,
        week_number: int,
        year: int = 2025,
    ) -> PipelineRunResult:
        """Ingests a single weekly sales file with DQ checks and idempotent fact upsert."""
        start_time = time.perf_counter()
        run_id = f"run_{year}_w{week_number:02d}_{uuid.uuid4().hex[:8]}"
        file_name = file_path.name

        # Calculate moving average row count for drift detection
        hist_avg = (
            sum(self._historical_row_counts) / len(self._historical_row_counts)
            if self._historical_row_counts
            else None
        )

        # 1. Run Data Quality Checks
        df, dq_report = self.dq_checker.validate_file(
            file_path=file_path,
            week_number=week_number,
            historical_avg_rows=hist_avg,
        )

        exec_time_ms = (time.perf_counter() - start_time) * 1000

        # 2. Critical DQ Failure Handling -> Block Load
        if dq_report.has_critical or df is None:
            status = "CRITICAL_FAILURE"
            self._log_pipeline_run(
                run_id=run_id,
                file_name=file_name,
                week_number=week_number,
                year=year,
                status=status,
                rows_staged=0,
                rows_loaded=0,
                dq_critical_count=len(dq_report.critical_issues),
                dq_warning_count=len(dq_report.warning_issues),
                execution_time_ms=exec_time_ms,
                logs=dq_report.summary_dict(),
            )
            self.alerts.send_critical_failure_alert(
                file_name=file_name,
                week_number=week_number,
                issues=[issue.__dict__ for issue in dq_report.critical_issues],
                execution_time_ms=exec_time_ms,
            )
            return PipelineRunResult(
                run_id=run_id,
                file_name=file_name,
                week_number=week_number,
                status=status,
                rows_staged=0,
                rows_loaded=0,
                dq_critical_count=len(dq_report.critical_issues),
                dq_warning_count=len(dq_report.warning_issues),
                execution_time_ms=exec_time_ms,
                report=dq_report,
                details={"action": "load_aborted", "reason": "critical_dq_failure"},
            )

        # 3. Warning DQ Handling vs Clean
        status = "LOADED_WITH_WARNINGS" if dq_report.has_warnings else "SUCCESS"
        if dq_report.has_warnings:
            self.alerts.send_warning_alert(
                file_name=file_name,
                week_number=week_number,
                issues=[issue.__dict__ for issue in dq_report.warning_issues],
                rows_loaded=len(df),
            )

        # 4. Ingest into Staging Table
        rows_staged = len(df)
        df["batch_id"] = run_id
        df["week_start_date"] = pd.to_datetime(df["week_start_date"]).dt.strftime("%Y-%m-%d")

        if self.db.is_postgres_available():
            import psycopg2.extras
            pg_conn = self.db._get_postgres_connection()
            with pg_conn.cursor() as cur:
                # Stage records
                records = [
                    (
                        r["week_start_date"],
                        int(r["hcp_id"]),
                        str(r["product_id"]),
                        str(r["territory_id"]),
                        int(r["units"]),
                        float(r["net_revenue"]),
                        run_id,
                    )
                    for _, r in df.iterrows()
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO stg_weekly_sales (week_start_date, hcp_id, product_id, territory_id, units, net_revenue, batch_id)
                    VALUES %s
                    """,
                    records,
                    page_size=2000,
                )
                # 5. Upsert from Staging into Fact Table (ON CONFLICT DO UPDATE)
                cur.execute(
                    """
                    INSERT INTO fact_weekly_sales (week_start_date, hcp_id, product_id, territory_id, units, net_revenue, loaded_at)
                    SELECT week_start_date, hcp_id, product_id, territory_id, units, net_revenue, CURRENT_TIMESTAMP
                    FROM stg_weekly_sales
                    WHERE batch_id = %s
                    ON CONFLICT (week_start_date, hcp_id, product_id, territory_id) DO UPDATE SET
                        units = EXCLUDED.units,
                        net_revenue = EXCLUDED.net_revenue,
                        loaded_at = CURRENT_TIMESTAMP;
                    """,
                    (run_id,),
                )
                pg_conn.commit()
            pg_conn.close()
        else:
            # DuckDB staging & upsert
            conn = self.db._get_duckdb_connection()
            conn.register("tmp_incoming_stg", df)
            conn.execute(
                """
                INSERT INTO stg_weekly_sales (week_start_date, hcp_id, product_id, territory_id, units, net_revenue, batch_id, ingested_at)
                SELECT CAST(week_start_date AS DATE), hcp_id, product_id, territory_id, units, net_revenue, batch_id, CURRENT_TIMESTAMP
                FROM tmp_incoming_stg;
                """
            )
            conn.execute(
                f"""
                INSERT OR REPLACE INTO fact_weekly_sales (week_start_date, hcp_id, product_id, territory_id, units, net_revenue, loaded_at)
                SELECT CAST(week_start_date AS DATE), hcp_id, product_id, territory_id, units, net_revenue, CURRENT_TIMESTAMP
                FROM stg_weekly_sales
                WHERE batch_id = '{run_id}';
                """
            )
            conn.unregister("tmp_incoming_stg")

        rows_loaded = rows_staged
        self._historical_row_counts.append(rows_loaded)
        exec_time_ms = (time.perf_counter() - start_time) * 1000

        # 6. Log Run into pipeline_runs
        self._log_pipeline_run(
            run_id=run_id,
            file_name=file_name,
            week_number=week_number,
            year=year,
            status=status,
            rows_staged=rows_staged,
            rows_loaded=rows_loaded,
            dq_critical_count=len(dq_report.critical_issues),
            dq_warning_count=len(dq_report.warning_issues),
            execution_time_ms=exec_time_ms,
            logs=dq_report.summary_dict(),
        )

        return PipelineRunResult(
            run_id=run_id,
            file_name=file_name,
            week_number=week_number,
            status=status,
            rows_staged=rows_staged,
            rows_loaded=rows_loaded,
            dq_critical_count=len(dq_report.critical_issues),
            dq_warning_count=len(dq_report.warning_issues),
            execution_time_ms=exec_time_ms,
            report=dq_report,
            details={"action": "loaded_to_fact", "upsert_status": "complete"},
        )

    def _log_pipeline_run(
        self,
        run_id: str,
        file_name: str,
        week_number: int,
        year: int,
        status: str,
        rows_staged: int,
        rows_loaded: int,
        dq_critical_count: int,
        dq_warning_count: int,
        execution_time_ms: float,
        logs: Dict[str, Any],
    ) -> None:
        """Records pipeline run audit record in pipeline_runs table."""
        try:
            if self.db.is_postgres_available():
                self.db.execute_query(
                    """
                    INSERT INTO pipeline_runs (
                        run_id, file_name, week_number, year, status,
                        rows_staged, rows_loaded, dq_critical_count, dq_warning_count,
                        execution_time_ms, logs
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        file_name,
                        week_number,
                        year,
                        status,
                        rows_staged,
                        rows_loaded,
                        dq_critical_count,
                        dq_warning_count,
                        execution_time_ms,
                        json.dumps(logs),
                    ),
                )
            else:
                conn = self.db._get_duckdb_connection()
                logs_json = json.dumps(logs)
                conn.execute(
                    """
                    INSERT INTO pipeline_runs (
                        run_id, file_name, week_number, year, status,
                        rows_staged, rows_loaded, dq_critical_count, dq_warning_count,
                        execution_time_ms, logs
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        file_name,
                        week_number,
                        year,
                        status,
                        rows_staged,
                        rows_loaded,
                        dq_critical_count,
                        dq_warning_count,
                        execution_time_ms,
                        logs_json,
                    ],
                )
        except Exception as e:
            logger.error(f"Failed to log pipeline run: {e}")
