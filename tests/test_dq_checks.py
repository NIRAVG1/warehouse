"""Unit and integration tests for Data Quality validation engine."""
from pathlib import Path
import pandas as pd
import pytest

from pipeline.dq_checks import DataQualityChecker, DQReport, REQUIRED_COLUMNS


@pytest.fixture
def dq_checker():
    valid_territories = {"CA-T1", "CA-T2", "TX-T1", "TX-T2", "NY-T1"}
    valid_products = {"CARDIVEX", "GLUCORA", "RESPIRA", "NEUROLIN", "ONCORA", "DERMACLEAR"}
    return DataQualityChecker(
        valid_territories=valid_territories,
        valid_products=valid_products,
        expected_min_rows=100,
    )


@pytest.fixture
def clean_dataframe():
    return pd.DataFrame({
        "week_start_date": ["2025-01-06"] * 5,
        "hcp_id": [1000000001, 1000000002, 1000000003, 1000000004, 1000000005],
        "product_id": ["CARDIVEX", "GLUCORA", "RESPIRA", "NEUROLIN", "ONCORA"],
        "territory_id": ["CA-T1", "CA-T2", "TX-T1", "TX-T2", "NY-T1"],
        "units": [10, 20, 15, 5, 8],
        "net_revenue": [420.0, 1160.0, 1125.0, 600.0, 3840.0],
    })


def test_clean_file_passes(tmp_path, dq_checker, clean_dataframe):
    # Repeat clean records to exceed expected_min_rows
    df = pd.concat([clean_dataframe] * 25, ignore_index=True)
    df["hcp_id"] = range(1000000000, 1000000000 + len(df))
    file_path = tmp_path / "sales_clean.csv"
    df.to_csv(file_path, index=False)

    df_res, report = dq_checker.validate_file(file_path, week_number=1)
    assert report.is_valid is True
    assert len(report.critical_issues) == 0
    assert len(report.warning_issues) == 0
    assert report.total_rows == len(df)


def test_missing_column_fails(tmp_path, dq_checker, clean_dataframe):
    df_missing = clean_dataframe.drop(columns=["net_revenue"])
    file_path = tmp_path / "sales_missing_col.csv"
    df_missing.to_csv(file_path, index=False)

    _, report = dq_checker.validate_file(file_path, week_number=15)
    assert report.is_valid is False
    assert any(i.issue_type == "missing_column" for i in report.critical_issues)


def test_null_keys_fail(tmp_path, dq_checker, clean_dataframe):
    df_null = clean_dataframe.copy()
    df_null.loc[0, "hcp_id"] = None
    file_path = tmp_path / "sales_nulls.csv"
    df_null.to_csv(file_path, index=False)

    _, report = dq_checker.validate_file(file_path, week_number=22)
    assert report.is_valid is False
    assert any(i.issue_type == "null_keys" for i in report.critical_issues)


def test_duplicate_rows_fail(tmp_path, dq_checker, clean_dataframe):
    df_dup = pd.concat([clean_dataframe, clean_dataframe.iloc[[0]]], ignore_index=True)
    file_path = tmp_path / "sales_duplicates.csv"
    df_dup.to_csv(file_path, index=False)

    _, report = dq_checker.validate_file(file_path, week_number=10)
    assert report.is_valid is False
    assert any(i.issue_type == "duplicate_rows" for i in report.critical_issues)


def test_unknown_territory_fails(tmp_path, dq_checker, clean_dataframe):
    df_unknown = clean_dataframe.copy()
    df_unknown.loc[0, "territory_id"] = "ZZ-T9"
    file_path = tmp_path / "sales_unknown_terr.csv"
    df_unknown.to_csv(file_path, index=False)

    _, report = dq_checker.validate_file(file_path, week_number=33)
    assert report.is_valid is False
    assert any(i.issue_type == "unknown_territory" for i in report.critical_issues)


def test_negative_units_raises_warning(tmp_path, dq_checker, clean_dataframe):
    df_neg = clean_dataframe.copy()
    df_neg.loc[0, "units"] = -5
    # Expand to meet row minimum
    df_neg = pd.concat([df_neg] * 25, ignore_index=True)
    df_neg["hcp_id"] = range(1000000000, 1000000000 + len(df_neg))
    file_path = tmp_path / "sales_neg.csv"
    df_neg.to_csv(file_path, index=False)

    _, report = dq_checker.validate_file(file_path, week_number=48)
    # Warnings do not block is_valid
    assert report.is_valid is True
    assert len(report.critical_issues) == 0
    assert any(i.issue_type == "negative_units" for i in report.warning_issues)


def test_row_count_drift_fails(tmp_path, dq_checker, clean_dataframe):
    # Only 5 rows against expected 100
    file_path = tmp_path / "sales_truncated.csv"
    clean_dataframe.to_csv(file_path, index=False)

    _, report = dq_checker.validate_file(file_path, week_number=45, historical_avg_rows=500.0)
    assert report.is_valid is False
    assert any(i.issue_type == "truncated_file" for i in report.critical_issues)
