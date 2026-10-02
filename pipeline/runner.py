"""Full Pipeline Execution, Benchmarking, and DQ Audit."""
import logging
import time
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
from tabulate import tabulate

from config.settings import settings
from db.connection import get_db
from db.schema import init_database
from pipeline.ingest import SalesIngestionPipeline, PipelineRunResult

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self):
        self.db = get_db()
        self.pipeline = SalesIngestionPipeline()

    def run_all_weeks(self, incoming_dir: Path = settings.DATA_DIR / "incoming") -> Tuple[List[PipelineRunResult], Dict[str, any]]:
        """Processes all 52 weekly files in incoming_dir in chronological order."""
        files = sorted(incoming_dir.glob("sales_*_w*.csv"))
        if not files:
            raise FileNotFoundError(f"No incoming sales files found in {incoming_dir}")

        logger.info(f"Starting pipeline run across {len(files)} weekly sales files...")
        start_time = time.perf_counter()
        results: List[PipelineRunResult] = []

        for f in files:
            # Extract week number from filename (e.g. sales_2025_w01.csv -> 1)
            stem = f.stem
            week_num = int(stem.split("_w")[-1])
            year_num = int(stem.split("_")[1])

            res = self.pipeline.ingest_weekly_file(
                file_path=f,
                week_number=week_num,
                year=year_num,
            )
            results.append(res)

        total_duration = time.perf_counter() - start_time

        # Compile metrics
        success_count = sum(1 for r in results if r.status == "SUCCESS")
        warning_count = sum(1 for r in results if r.status == "LOADED_WITH_WARNINGS")
        critical_count = sum(1 for r in results if r.status == "CRITICAL_FAILURE")
        total_rows_loaded = sum(r.rows_loaded for r in results)

        summary_metrics = {
            "total_files_processed": len(files),
            "clean_loads": success_count,
            "loads_with_warnings": warning_count,
            "blocked_loads": critical_count,
            "total_rows_loaded": total_rows_loaded,
            "total_duration_sec": total_duration,
            "avg_file_duration_ms": (total_duration / len(files)) * 1000,
            # Manual baseline comparison for resume metrics (manual verification & loading ~45 mins/week = 2340 mins for 52 weeks)
            "manual_baseline_hours": 39.0,
            "automated_pipeline_seconds": total_duration,
            "speedup_factor": (39.0 * 3600) / max(total_duration, 0.001),
        }

        return results, summary_metrics

    def verify_idempotency(self, sample_file: Path) -> Dict[str, any]:
        """Runs ingestion twice on the same file to prove zero duplicate creation."""
        week_num = int(sample_file.stem.split("_w")[-1])
        year_num = int(sample_file.stem.split("_")[1])

        # Get initial count
        df_before = self.db.execute_query("SELECT COUNT(*) AS total_rows FROM fact_weekly_sales")
        count_before = int(df_before["total_rows"].iloc[0]) if not df_before.empty else 0

        # Run 1
        res1 = self.pipeline.ingest_weekly_file(sample_file, week_number=week_num, year=year_num)
        df_after1 = self.db.execute_query("SELECT COUNT(*) AS total_rows FROM fact_weekly_sales")
        count_after1 = int(df_after1["total_rows"].iloc[0])

        # Run 2 on same file
        res2 = self.pipeline.ingest_weekly_file(sample_file, week_number=week_num, year=year_num)
        df_after2 = self.db.execute_query("SELECT COUNT(*) AS total_rows FROM fact_weekly_sales")
        count_after2 = int(df_after2["total_rows"].iloc[0])

        return {
            "file": sample_file.name,
            "count_before": count_before,
            "count_after_first_run": count_after1,
            "count_after_second_run": count_after2,
            "idempotent": count_after1 == count_after2,
            "duplicate_increase": count_after2 - count_after1,
        }

    def verify_against_ground_truth_dq(self, results: List[PipelineRunResult], dq_ground_truth_path: Path = settings.DATA_DIR / "ground_truth" / "dq_issues.csv") -> Dict[str, any]:
        """Compares pipeline DQ detections against ground truth dq_issues.csv."""
        if not dq_ground_truth_path.exists():
            return {"error": "Ground truth dq_issues.csv not found"}

        gt_df = pd.read_csv(dq_ground_truth_path)
        expected_issues = {int(r["week"]): r for _, r in gt_df.iterrows()}

        detected_issues = {}
        for r in results:
            all_issues = r.report.critical_issues + r.report.warning_issues
            if all_issues:
                detected_issues[r.week_number] = {
                    "week": r.week_number,
                    "file": r.file_name,
                    "status": r.status,
                    "issues": [i.issue_type for i in all_issues],
                    "severities": [i.severity for i in all_issues],
                }

        exact_match = set(expected_issues.keys()) == set(detected_issues.keys())
        return {
            "expected_weeks_with_issues": sorted(list(expected_issues.keys())),
            "detected_weeks_with_issues": sorted(list(detected_issues.keys())),
            "exact_match": exact_match,
            "expected_count": len(expected_issues),
            "detected_count": len(detected_issues),
            "details": detected_issues,
        }
