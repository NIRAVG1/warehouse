"""CLI script to execute the full 52-week ingestion pipeline, test idempotency, audit DQ, and generate reports."""
import logging
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tabulate import tabulate

from config.settings import settings
from db.schema import init_database
from pipeline.runner import PipelineRunner
from pipeline.reporting import WeeklyReportGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("================================================================================")
    logger.info("PHARMA COMMERCIAL DATA WAREHOUSE: 52-WEEK PIPELINE INGESTION & AUDIT")
    logger.info("================================================================================")

    # 1. Initialize schema and seed dimensions
    logger.info("\n--- Phase 1: Database Schema Migration & Dimension Seeding ---")
    init_database()

    # 2. Ingest 52 weekly files
    logger.info("\n--- Phase 2: Processing 52 Weekly Ingestion Files ---")
    runner = PipelineRunner()
    results, metrics = runner.run_all_weeks()

    # 3. Print pipeline execution summary table
    table_data = []
    for r in results:
        issues_str = ", ".join([f"{i.issue_type} ({i.severity})" for i in (r.report.critical_issues + r.report.warning_issues)])
        table_data.append([
            r.week_number,
            r.file_name,
            r.status,
            f"{r.rows_staged:,}",
            f"{r.rows_loaded:,}",
            issues_str if issues_str else "Clean",
            f"{r.execution_time_ms:.1f}ms"
        ])

    print("\n" + tabulate(
        table_data,
        headers=["Week", "File Name", "Status", "Staged", "Loaded", "DQ Findings", "Latency"],
        tablefmt="github"
    ))

    # 4. Phase 3: Idempotency & Upsert Verification
    logger.info("\n--- Phase 3: Testing Ingestion Idempotency (Zero-Duplicate Upsert) ---")
    sample_file = settings.DATA_DIR / "incoming" / "sales_2025_w01.csv"
    idempotency_res = runner.verify_idempotency(sample_file)
    logger.info(f"Idempotency Test Result for {sample_file.name}:")
    logger.info(f"  • Count Before: {idempotency_res['count_before']:,}")
    logger.info(f"  • Count After 1st Run: {idempotency_res['count_after_first_run']:,}")
    logger.info(f"  • Count After 2nd Run: {idempotency_res['count_after_second_run']:,}")
    logger.info(f"  • Duplicate Records Added: {idempotency_res['duplicate_increase']} (PASSED: {idempotency_res['idempotent']})")

    # 5. Phase 4: Ground Truth Data Quality Verification
    logger.info("\n--- Phase 4: Validating DQ Detections against Ground Truth (dq_issues.csv) ---")
    dq_val = runner.verify_against_ground_truth_dq(results)
    logger.info(f"Expected Issue Weeks: {dq_val['expected_weeks_with_issues']}")
    logger.info(f"Detected Issue Weeks: {dq_val['detected_weeks_with_issues']}")
    logger.info(f"Exact Ground Truth Match: {dq_val['exact_match']} (Detected {dq_val['detected_count']}/{dq_val['expected_count']} issues)")

    # 6. Phase 5: Generate Executive Multi-Tab Excel Report
    logger.info("\n--- Phase 5: Generating Executive Excel Report & KPI Dashboard ---")
    reporter = WeeklyReportGenerator()
    report_file = reporter.generate_excel_report()
    logger.info(f"Report saved to: {report_file}")

    # 7. Summary & Resume Metrics
    logger.info("\n================================================================================")
    logger.info("PIPELINE PERFORMANCE & RESUME BENCHMARKS")
    logger.info("================================================================================")
    logger.info(f"• Total Weekly Files Processed: {metrics['total_files_processed']}")
    logger.info(f"• Successful Clean Loads: {metrics['clean_loads']}/52")
    logger.info(f"• Loaded with Warnings: {metrics['loads_with_warnings']}/52")
    logger.info(f"• Blocked Critical Anomalies: {metrics['blocked_loads']}/52")
    logger.info(f"• Total Fact Records Loaded: {metrics['total_rows_loaded']:,}")
    logger.info(f"• Total Automated Run Time: {metrics['total_duration_sec']:.2f} seconds")
    logger.info(f"• Manual Processing Baseline: {metrics['manual_baseline_hours']} hours (~45 mins/file)")
    logger.info(f"• Speedup / Productivity Increase: {metrics['speedup_factor']:.0f}x faster")
    logger.info("================================================================================\n")


if __name__ == "__main__":
    main()
