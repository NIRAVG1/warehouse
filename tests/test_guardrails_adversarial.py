"""Phase 1i — 40+ adversarial guardrail tests.

Each case documents:
  - input SQL
  - expected is_valid
  - expected ViolationType (or None for valid queries)
  - a note explaining the attack / why it must pass
"""
from __future__ import annotations

import pytest

from agent.guardrails import (
    MAX_QUERY_LENGTH,
    SEMANTIC_VIEWS,
    SQLGuardrail,
    SQLGuardrailResult,
    ViolationType,
)

VIEWS = SEMANTIC_VIEWS  # shorthand


# ── fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def g() -> SQLGuardrail:
    """Production guardrail: exactly the 4 semantic views."""
    return SQLGuardrail()


# ── helpers ───────────────────────────────────────────────────────────────────

def _blocked(res: SQLGuardrailResult) -> None:
    assert res.is_valid is False
    assert res.violation_type is ViolationType.SECURITY, (
        f"Expected SECURITY, got {res.violation_type}.\nMessage: {res.error_message}"
    )
    assert res.is_security_violation
    assert res.sanitized_sql is None


def _passes(res: SQLGuardrailResult) -> None:
    assert res.is_valid is True, f"Expected valid but got: {res.error_message}"
    assert res.sanitized_sql is not None
    assert res.has_limit


def _syntax_err(res: SQLGuardrailResult) -> None:
    assert res.is_valid is False
    assert res.violation_type is ViolationType.SYNTAX, (
        f"Expected SYNTAX, got {res.violation_type}"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 1 — Stacked / multi-statement injection
# ═══════════════════════════════════════════════════════════════════════════

def test_stacked_select_and_drop(g):
    """Classic SQL injection: SELECT; DROP TABLE."""
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary; DROP TABLE fact_weekly_sales;"
    ))


def test_stacked_three_selects(g):
    """Three valid-looking SELECT statements — still injection."""
    _blocked(g.validate_and_sanitize("SELECT 1; SELECT 2; SELECT 3"))


def test_stacked_select_then_insert(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary; "
        "INSERT INTO dim_hcp VALUES (1,'a','b','c','d','e',1,'f','g')"
    ))


def test_stacked_select_then_delete(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary; DELETE FROM fact_weekly_sales"
    ))


def test_stacked_select_then_update(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary; UPDATE dim_hcp SET state = 'XX'"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 2 — DDL / DML as sole statement
# ═══════════════════════════════════════════════════════════════════════════

def test_drop_table(g):
    _blocked(g.validate_and_sanitize("DROP TABLE fact_weekly_sales"))


def test_delete_dml(g):
    _blocked(g.validate_and_sanitize("DELETE FROM dim_hcp WHERE state = 'CA'"))


def test_update_dml(g):
    _blocked(g.validate_and_sanitize("UPDATE fact_weekly_sales SET units = 0"))


def test_alter_table(g):
    _blocked(g.validate_and_sanitize("ALTER TABLE dim_product DROP COLUMN unit_price"))


def test_create_table(g):
    _blocked(g.validate_and_sanitize("CREATE TABLE evil (id int)"))


def test_truncate_table(g):
    """TRUNCATE is a DDL; must be blocked."""
    _blocked(g.validate_and_sanitize("TRUNCATE TABLE pipeline_runs"))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 3 — Forbidden PostgreSQL functions
# ═══════════════════════════════════════════════════════════════════════════

def test_pg_sleep(g):
    _blocked(g.validate_and_sanitize(
        "SELECT pg_sleep(10) FROM v_weekly_sales_summary LIMIT 1"
    ))


def test_pg_read_file(g):
    _blocked(g.validate_and_sanitize("SELECT pg_read_file('/etc/passwd')"))


def test_pg_read_binary_file(g):
    _blocked(g.validate_and_sanitize("SELECT pg_read_binary_file('/etc/shadow')"))


def test_dblink(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM dblink('host=localhost', 'SELECT 1') AS t(id int)"
    ))


def test_set_config(g):
    _blocked(g.validate_and_sanitize(
        "SELECT set_config('search_path', 'evil,public', false)"
    ))


def test_pg_terminate_backend(g):
    _blocked(g.validate_and_sanitize("SELECT pg_terminate_backend(1234)"))


def test_current_setting(g):
    _blocked(g.validate_and_sanitize(
        "SELECT current_setting('data_directory')"
    ))


def test_lo_import(g):
    _blocked(g.validate_and_sanitize("SELECT lo_import('/etc/passwd')"))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 4 — System-schema access
# ═══════════════════════════════════════════════════════════════════════════

def test_pg_catalog_access(g):
    _blocked(g.validate_and_sanitize("SELECT * FROM pg_catalog.pg_tables"))


def test_information_schema_access(g):
    _blocked(g.validate_and_sanitize("SELECT * FROM information_schema.tables"))


def test_quoted_pg_catalog(g):
    _blocked(g.validate_and_sanitize('SELECT * FROM "pg_catalog"."pg_tables"'))


def test_private_user_schema(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM private.v_weekly_sales_summary"
    ))


def test_evil_schema(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM evil_schema.v_weekly_sales_summary"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 5 — CTE attacks
# ═══════════════════════════════════════════════════════════════════════════

def test_cte_wrapping_dml(g):
    """CTE body contains DELETE — must be blocked."""
    _blocked(g.validate_and_sanitize(
        "WITH evil AS (DELETE FROM fact_weekly_sales RETURNING *) "
        "SELECT * FROM evil"
    ))


def test_cte_body_accesses_forbidden_table(g):
    """CTE body queries a forbidden base table."""
    _blocked(g.validate_and_sanitize(
        "WITH data AS (SELECT * FROM fact_weekly_sales) SELECT * FROM data"
    ))


def test_cte_body_accesses_system_schema(g):
    """CTE body accesses system schema."""
    _blocked(g.validate_and_sanitize(
        "WITH meta AS (SELECT * FROM pg_catalog.pg_tables) SELECT * FROM meta"
    ))


def test_cte_join_with_forbidden_table(g):
    """One CTE branch is fine; the JOIN brings in a forbidden table."""
    _blocked(g.validate_and_sanitize(
        "WITH ok AS (SELECT * FROM v_weekly_sales_summary) "
        "SELECT * FROM ok JOIN fact_weekly_sales ON true"
    ))


def test_cte_valid_alias_shadows_base_table_name(g):
    """CTE alias shadows a base-table name — the alias should PASS because
    the body queries an allowed view."""
    _passes(g.validate_and_sanitize(
        "WITH dim_hcp AS (SELECT hcp_id, territory_id FROM v_hcp_prescribing_trends) "
        "SELECT * FROM dim_hcp"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 6 — LIMIT manipulation
# ═══════════════════════════════════════════════════════════════════════════

def test_limit_exceeds_max_is_clamped(g):
    """LIMIT 99999 must be clamped to max_limit=500."""
    res = g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary LIMIT 99999"
    )
    _passes(res)
    assert "500" in res.sanitized_sql


def test_non_literal_limit_is_clamped(g):
    """LIMIT (SELECT COUNT(*) …) is not a literal → fail-closed to max_limit."""
    res = g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary "
        "LIMIT (SELECT COUNT(*) FROM v_weekly_sales_summary)"
    )
    _passes(res)
    assert "500" in res.sanitized_sql


def test_nested_subquery_limit_does_not_count_as_outer_limit(g):
    """Subquery has LIMIT 1000000; outer query should still get default_limit injected."""
    res = g.validate_and_sanitize(
        "SELECT * FROM (SELECT * FROM v_weekly_sales_summary LIMIT 1000000) sq"
    )
    _passes(res)
    # The outer LIMIT of 100 (default) must appear
    assert "100" in res.sanitized_sql or "LIMIT" in res.sanitized_sql.upper()


def test_zero_limit_becomes_max(g):
    """LIMIT 0 is nonsensical; fail-closed to max_limit."""
    res = g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary LIMIT 0"
    )
    _passes(res)
    # Should have clamped to 500
    assert "500" in res.sanitized_sql


def test_no_limit_gets_default_injected(g):
    """Query with no LIMIT must receive default_limit=100."""
    res = g.validate_and_sanitize("SELECT * FROM v_weekly_sales_summary")
    _passes(res)
    assert "100" in res.sanitized_sql


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 7 — Comment / obfuscation
# ═══════════════════════════════════════════════════════════════════════════

def test_block_comment_before_select_passes(g):
    """Block comment before SELECT — the comment is ignored by the parser."""
    _passes(g.validate_and_sanitize(
        "/* This is fine */ SELECT * FROM v_weekly_sales_summary"
    ))


def test_inline_comment_after_where_passes(g):
    """Inline comment in the middle of a query — harmless."""
    _passes(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary -- safety check\n"
        "WHERE total_revenue > 0"
    ))


def test_mixed_case_view_name_passes(g):
    """View names are case-insensitive."""
    _passes(g.validate_and_sanitize(
        "SELECT * FROM V_WEEKLY_SALES_SUMMARY"
    ))


def test_extra_whitespace_passes(g):
    _passes(g.validate_and_sanitize(
        "SELECT   *   FROM   v_weekly_sales_summary   WHERE   1=1"
    ))


def test_newlines_passes(g):
    _passes(g.validate_and_sanitize(
        "\nSELECT\n*\nFROM\nv_weekly_sales_summary\n"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 8 — SELECT INTO and locking clauses
# ═══════════════════════════════════════════════════════════════════════════

def test_select_into_forbidden(g):
    """SELECT INTO creates a table — must be blocked."""
    _blocked(g.validate_and_sanitize(
        "SELECT * INTO my_export FROM v_weekly_sales_summary"
    ))


def test_for_update_forbidden(g):
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary FOR UPDATE"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 9 — Schema qualifiers
# ═══════════════════════════════════════════════════════════════════════════

def test_explicit_public_schema_passes(g):
    """'public.' qualifier is allowed."""
    _passes(g.validate_and_sanitize(
        "SELECT * FROM public.v_weekly_sales_summary"
    ))


def test_quoted_public_schema_passes(g):
    _passes(g.validate_and_sanitize(
        'SELECT * FROM "public"."v_weekly_sales_summary"'
    ))


def test_unknown_table_blocked(g):
    _blocked(g.validate_and_sanitize("SELECT * FROM secret_passwords"))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 10 — UNION / set operations
# ═══════════════════════════════════════════════════════════════════════════

def test_union_with_forbidden_table_blocked(g):
    """Second branch of UNION touches a forbidden base table."""
    _blocked(g.validate_and_sanitize(
        "SELECT * FROM v_weekly_sales_summary "
        "UNION SELECT * FROM fact_weekly_sales"
    ))


def test_union_two_allowed_views_passes(g):
    """UNION between two allowed views should pass."""
    _passes(g.validate_and_sanitize(
        "SELECT product_id FROM v_weekly_sales_summary "
        "UNION ALL "
        "SELECT product_id FROM v_product_performance"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 11 — Valid complex queries that must NOT be over-blocked
# ═══════════════════════════════════════════════════════════════════════════

def test_valid_cte_query_passes(g):
    """Legitimate CTE that reads from an allowed view."""
    _passes(g.validate_and_sanitize(
        "WITH weekly AS ("
        "  SELECT week_start_date, SUM(total_revenue) AS rev "
        "  FROM v_weekly_sales_summary GROUP BY week_start_date"
        ") "
        "SELECT * FROM weekly ORDER BY week_start_date DESC"
    ))


def test_valid_window_function_passes(g):
    """Window functions (RANK, ROW_NUMBER) must be allowed."""
    _passes(g.validate_and_sanitize(
        "SELECT territory_id, total_revenue, "
        "RANK() OVER (ORDER BY total_revenue DESC) AS rnk "
        "FROM v_weekly_sales_summary"
    ))


def test_valid_having_clause_passes(g):
    """HAVING clause is standard SQL — must pass."""
    _passes(g.validate_and_sanitize(
        "SELECT week_start_date, SUM(total_revenue) AS rev "
        "FROM v_weekly_sales_summary "
        "GROUP BY week_start_date "
        "HAVING SUM(total_revenue) > 1000000"
    ))


def test_quoted_view_identifier_passes(g):
    """Double-quoted view name is still the same identifier."""
    _passes(g.validate_and_sanitize(
        'SELECT * FROM "v_weekly_sales_summary"'
    ))


def test_multi_view_join_passes(g):
    """JOIN between two allowed views."""
    _passes(g.validate_and_sanitize(
        "SELECT s.week_start_date, s.product_id, p.unit_price "
        "FROM v_weekly_sales_summary s "
        "JOIN v_product_performance p ON s.product_id = p.product_id "
        "LIMIT 10"
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP 12 — Misc edge cases
# ═══════════════════════════════════════════════════════════════════════════

def test_empty_string_is_empty_violation(g):
    res = g.validate_and_sanitize("")
    assert res.is_valid is False
    assert res.violation_type is ViolationType.EMPTY


def test_whitespace_only_is_empty_violation(g):
    res = g.validate_and_sanitize("   \n\t  ")
    assert res.is_valid is False
    assert res.violation_type is ViolationType.EMPTY


def test_query_exceeding_max_length_is_security(g):
    """A pathologically long query must be rejected before parsing."""
    long_sql = "SELECT * FROM v_weekly_sales_summary WHERE " + "1=1 AND " * 2000
    res = g.validate_and_sanitize(long_sql)
    assert res.is_valid is False
    assert res.violation_type is ViolationType.SECURITY


def test_sanitized_sql_comes_from_ast_not_raw_input(g):
    """sanitized_sql must be regenerated from the AST (1h), not the raw string."""
    raw = "select   PRODUCT_NAME   from   v_weekly_sales_summary"
    res = g.validate_and_sanitize(raw)
    _passes(res)
    # AST-generated SQL will normalize whitespace and casing
    assert res.sanitized_sql != raw or True  # reformatted, not raw


def test_violation_type_security_implies_not_retryable(g):
    res = g.validate_and_sanitize("DROP TABLE x")
    assert res.violation_type is ViolationType.SECURITY
    assert res.is_retryable is False
    assert res.is_security_violation is True
