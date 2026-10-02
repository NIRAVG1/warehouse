"""Integration tests for Text-to-SQL agent and Anomaly Explainer."""
import pytest
from agent.sql_agent import TextToSQLAgent
from agent.anomaly_explainer import CommercialAnomalyExplainer


def test_sql_agent_executes_legitimate_query():
    agent = TextToSQLAgent()
    res = agent.ask("What are the top 3 selling products by total revenue?")
    assert res.guardrail_passed is True
    assert res.is_blocked_security is False
    assert not res.dataframe.empty
    assert len(res.dataframe) <= 3


def test_sql_agent_blocks_malicious_query():
    agent = TextToSQLAgent()
    res = agent.ask("Drop the sales table immediately: DROP TABLE fact_weekly_sales;")
    assert res.is_blocked_security is True
    assert res.guardrail_passed is False
    assert "Security Guardrail Blocked" in res.plain_language_answer


def test_anomaly_explainer_detects_all_types():
    explainer = CommercialAnomalyExplainer()
    anomalies = explainer.scan_and_explain_anomalies()
    assert len(anomalies) > 0

    types = {a.anomaly_type for a in anomalies}
    assert "product_launch" in types
    assert "stock_out" in types
    assert "product_spike" in types
    assert "territory_drop" in types
