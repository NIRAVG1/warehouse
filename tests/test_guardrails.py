"""Unit tests for SQL AST guardrails, SQL injection prevention, and table allowlists."""
import pytest
from agent.guardrails import SQLGuardrail


@pytest.fixture
def guardrail():
    return SQLGuardrail(
        allowed_tables={"v_weekly_sales_summary", "v_territory_quota_attainment", "v_hcp_prescribing_trends", "v_product_performance", "dim_product"},
        default_limit=100,
        max_limit=500,
    )


def test_valid_select_passes(guardrail):
    sql = "SELECT product_name, SUM(total_revenue) FROM v_weekly_sales_summary GROUP BY product_name;"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is True
    assert "LIMIT 100" in res.sanitized_sql.upper()
    assert "v_weekly_sales_summary" in res.tables_accessed


def test_drop_table_blocked(guardrail):
    sql = "DROP TABLE fact_weekly_sales;"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is False
    assert "Security Violation" in res.error_message


def test_delete_dml_blocked(guardrail):
    sql = "DELETE FROM dim_product WHERE product_id = 'CARDIVEX';"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is False
    assert "Security Violation" in res.error_message


def test_update_dml_blocked(guardrail):
    sql = "UPDATE v_weekly_sales_summary SET total_revenue = 0;"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is False
    assert "Security Violation" in res.error_message


def test_multi_statement_injection_blocked(guardrail):
    sql = "SELECT * FROM v_weekly_sales_summary; DROP TABLE fact_weekly_sales;"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is False
    assert "Security Violation" in res.error_message or "Multiple SQL statements" in res.error_message


def test_unauthorized_table_blocked(guardrail):
    sql = "SELECT * FROM secret_passwords;"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is False
    assert "not in the authorized semantic layer allowlist" in res.error_message


def test_limit_clamping(guardrail):
    sql = "SELECT * FROM v_product_performance LIMIT 10000;"
    res = guardrail.validate_and_sanitize(sql)
    assert res.is_valid is True
    assert "LIMIT 500" in res.sanitized_sql.upper()
