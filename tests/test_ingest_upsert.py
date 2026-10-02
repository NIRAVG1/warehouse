"""Integration tests for weekly sales ingestion and idempotent upsert."""
from pathlib import Path
import pytest

from config.settings import settings
from db.connection import get_db
from db.schema import init_database
from pipeline.ingest import SalesIngestionPipeline
from pipeline.runner import PipelineRunner


@pytest.fixture(scope="module", autouse=True)
def setup_dw():
    init_database()


def test_idempotent_upsert_zero_duplicates():
    runner = PipelineRunner()
    sample_file = settings.DATA_DIR / "incoming" / "sales_2025_w01.csv"
    if not sample_file.exists():
        pytest.skip("sales_2025_w01.csv not found")

    res = runner.verify_idempotency(sample_file)
    assert res["idempotent"] is True
    assert res["duplicate_increase"] == 0
    assert res["count_after_first_run"] == res["count_after_second_run"]


def test_critical_failure_blocks_load():
    pipeline = SalesIngestionPipeline()
    # Week 15 has missing net_revenue column
    w15_file = settings.DATA_DIR / "incoming" / "sales_2025_w15.csv"
    if not w15_file.exists():
        pytest.skip("sales_2025_w15.csv not found")

    res = pipeline.ingest_weekly_file(w15_file, week_number=15)
    assert res.status == "CRITICAL_FAILURE"
    assert res.rows_loaded == 0
    assert res.dq_critical_count > 0


def test_warning_allows_load():
    pipeline = SalesIngestionPipeline()
    # Week 48 has negative units warning
    w48_file = settings.DATA_DIR / "incoming" / "sales_2025_w48.csv"
    if not w48_file.exists():
        pytest.skip("sales_2025_w48.csv not found")

    res = pipeline.ingest_weekly_file(w48_file, week_number=48)
    assert res.status == "LOADED_WITH_WARNINGS"
    assert res.rows_loaded > 0
    assert res.dq_warning_count > 0
