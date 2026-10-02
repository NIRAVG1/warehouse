"""Apache Airflow DAG for Weekly Commercial Sales ETL and Data Quality Auditing."""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

default_args = {
    "owner": "commercial_ops",
    "depends_on_past": False,
    "start_date": datetime(2025, 1, 1),
    "email": ["alerts@commercialops.pharma.internal"],
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def execute_weekly_ingestion_task(**kwargs):
    """Executes weekly ingestion and logs audit metrics to pipeline_runs."""
    from pipeline.runner import PipelineRunner
    from pipeline.reporting import WeeklyReportGenerator

    runner = PipelineRunner()
    results, metrics = runner.run_all_weeks()

    # Generate executive weekly report
    reporter = WeeklyReportGenerator()
    report_path = reporter.generate_excel_report()

    print(f"Airflow Task Complete. Processed {metrics['total_files_processed']} files. Report: {report_path}")
    return metrics


with DAG(
    "pharma_commercial_weekly_pipeline",
    default_args=default_args,
    description="Automated weekly sales ingestion, DQ validation, and fact table upsert",
    schedule_interval="0 6 * * 1",  # Every Monday at 06:00 UTC
    catchup=False,
    max_active_runs=1,
    tags=["commercial", "pharma", "warehouse", "etl"],
) as dag:

    ingest_task = PythonOperator(
        task_id="ingest_and_validate_weekly_sales",
        python_callable=execute_weekly_ingestion_task,
        provide_context=True,
    )

    ingest_task
