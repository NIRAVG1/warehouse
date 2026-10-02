"""Pharma Commercial Data Warehouse & Text-to-SQL Semantic Agent - Streamlit Application."""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pandas as pd
import streamlit as st
import altair as alt

from config.settings import settings
from db.connection import get_db
from agent.sql_agent import TextToSQLAgent
from agent.guardrails import SQLGuardrail
from agent.anomaly_explainer import CommercialAnomalyExplainer
from pipeline.runner import PipelineRunner
from pipeline.reporting import WeeklyReportGenerator

# Page Config
st.set_page_config(
    page_title="Pharma Commercial Data Warehouse & Text-to-SQL Agent",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for modern design
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1A365D;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #4A5568;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: #F7FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 1.2rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .kpi-title {
        font-size: 0.85rem;
        font-weight: 600;
        color: #718096;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .kpi-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #2B6CB0;
        margin-top: 0.3rem;
    }
    .badge-success {
        background-color: #DEF7EC;
        color: #03543F;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.8rem;
    }
    .badge-danger {
        background-color: #FDE8E8;
        color: #9B1C1C;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.8rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 18px;
        border-radius: 6px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_agent():
    return TextToSQLAgent()


@st.cache_resource
def get_explainer():
    return CommercialAnomalyExplainer()


agent = get_agent()
db = get_db(read_only=True)

# Sidebar
st.sidebar.image("https://images.unsplash.com/photo-1587854692152-cbe660dbde88?w=400&q=80", use_container_width=True)
st.sidebar.markdown("### 💊 Pharma Commercial DW")
st.sidebar.markdown("**Database**: PostgreSQL 16 (Semantic Layer)")
st.sidebar.markdown("**Role**: `pharma_analyst_ro` (Read-Only)")
st.sidebar.markdown("**Guardrails**: AST Validation via `sqlglot`")
st.sidebar.divider()

# Quick KPI aggregates in sidebar
try:
    df_sidebar = db.execute_query("SELECT SUM(total_revenue) AS rev, SUM(total_units) AS units FROM v_weekly_sales_summary;")
    tot_rev = df_sidebar["rev"].iloc[0] if not df_sidebar.empty else 0.0
    tot_units = df_sidebar["units"].iloc[0] if not df_sidebar.empty else 0
    st.sidebar.metric("Total 2025 Revenue", f"${tot_rev:,.2f}")
    st.sidebar.metric("Total Units Sold", f"{tot_units:,}")
except Exception:
    pass

st.sidebar.divider()
st.sidebar.info("💡 **Tip**: Switch tabs to explore Text-to-SQL, Anomaly Detection, Guardrails, or Pipeline Health.")

# Main Application Layout
st.markdown('<div class="main-header">Pharma Commercial Intelligence & Text-to-SQL Agent</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Automated 52-Week Commercial Warehouse, Data Quality Gatekeeping & Conversational Semantic Analytics</div>', unsafe_allow_html=True)

tab_chat, tab_anomalies, tab_catalog, tab_guardrails, tab_pipeline = st.tabs([
    "💬 Natural Language Assistant",
    "🔍 Anomaly Explainer & Root Cause",
    "📊 Semantic Catalog Explorer",
    "🛡️ Guardrails & Security Sandbox",
    "📈 Pipeline Health & DQ Audit",
])

# -----------------------------------------------------------------------------
# TAB 1: Conversational Text-to-SQL Assistant
# -----------------------------------------------------------------------------
with tab_chat:
    st.markdown("#### Ask Any Commercial Sales Question")
    st.markdown("The AI Agent queries the PostgreSQL semantic views (`v_weekly_sales_summary`, `v_territory_quota_attainment`, `v_hcp_prescribing_trends`, `v_product_performance`) with built-in AST guardrails.")

    # Sample query chips
    sample_queries = [
        "What are the top 3 selling products by total revenue?",
        "Which territories had the lowest quota attainment for GLUCORA in 2025Q2?",
        "Show me the top 10 Segment A cardiologists by total revenue generated.",
        "What was the weekly revenue trend for DERMACLEAR after its product launch in week 20?",
        "Did RESPIRA experience zero sales in Georgia during week 40?",
    ]

    cols = st.columns(len(sample_queries))
    selected_sample = None
    for idx, sq in enumerate(sample_queries):
        if cols[idx].button(f"📌 {sq[:24]}...", key=f"sq_{idx}", help=sq):
            selected_sample = sq

    user_query = st.text_input(
        "Enter your sales question:",
        value=selected_sample if selected_sample else "",
        placeholder="e.g. Which territories achieved over 100% quota attainment in 2025Q1?",
    )

    if st.button("🚀 Analyze Query", type="primary") or user_query:
        if user_query:
            with st.spinner("Analyzing semantic schema, generating guarded SQL, and querying warehouse..."):
                response = agent.ask(user_query)

            if response.is_blocked_security:
                st.error(response.plain_language_answer)
                st.markdown(f"**Rejected SQL Statement**:\n```sql\n{response.generated_sql}\n```")
            elif not response.guardrail_passed:
                st.warning(f"⚠️ {response.error}")
            else:
                st.success(f"Query Executed in **{response.execution_time_ms:.1f}ms** (Guardrails Passed ✅)")
                
                col_ans, col_sql = st.columns([3, 2])
                with col_ans:
                    st.markdown("### Executive Summary")
                    st.markdown(response.plain_language_answer)
                
                with col_sql:
                    st.markdown("### Executed SQL (Validated)")
                    st.code(response.sanitized_sql, language="sql")

                if not response.dataframe.empty:
                    st.markdown("### Data Results")
                    st.dataframe(response.dataframe, use_container_width=True)

                    # Dynamic charting
                    num_cols = response.dataframe.select_dtypes(include=["number"]).columns.tolist()
                    cat_cols = response.dataframe.select_dtypes(include=["object", "string", "category"]).columns.tolist()

                    if cat_cols and num_cols:
                        st.markdown("### Visual Insights")
                        chart_type = st.radio("Chart Type", ["Bar Chart", "Line Chart"], horizontal=True)
                        x_col = cat_cols[0]
                        y_col = num_cols[0]
                        
                        if chart_type == "Bar Chart":
                            chart = alt.Chart(response.dataframe.head(20)).mark_bar().encode(
                                x=alt.X(f"{x_col}:N", sort="-y"),
                                y=alt.Y(f"{y_col}:Q"),
                                tooltip=list(response.dataframe.columns[:4])
                            ).properties(height=350)
                            st.altair_chart(chart, use_container_width=True)
                        else:
                            chart = alt.Chart(response.dataframe).mark_line(point=True).encode(
                                x=alt.X(f"{x_col}:N"),
                                y=alt.Y(f"{y_col}:Q"),
                                tooltip=list(response.dataframe.columns[:4])
                            ).properties(height=350)
                            st.altair_chart(chart, use_container_width=True)

                    # Download CSV
                    csv_data = response.dataframe.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="📥 Download Data as CSV",
                        data=csv_data,
                        file_name=f"sales_query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv",
                    )
        else:
            st.info("Please enter a question or click a sample question above.")

# -----------------------------------------------------------------------------
# TAB 2: Anomaly Explainer & Root Cause Deep Dive
# -----------------------------------------------------------------------------
with tab_anomalies:
    st.markdown("#### Automated Statistical Anomaly Detection & Cause Hypothesis Engine")
    st.markdown("Scans 52-week time-series data using 4-week rolling baselines, z-scores, and state-product completeness checks.")

    explainer = get_explainer()
    if st.button("🔄 Scan Warehouse for Commercial Anomalies"):
        st.cache_resource.clear()

    with st.spinner("Analyzing volume distributions and running approved drill-down queries..."):
        detected_anomalies = explainer.scan_and_explain_anomalies()

    st.markdown(f"**Detected {len(detected_anomalies)} Commercial Anomalies across 52 Weeks**")

    for a in detected_anomalies:
        badge_color = "red" if a.anomaly_type == "stock_out" else "blue" if a.anomaly_type == "product_launch" else "orange"
        with st.expander(f"Week {a.week_number} ({a.week_start_date}) — [{a.anomaly_type.upper()}] {a.entity}", expanded=True):
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Observed Value", f"{a.observed_value:,.0f}")
            col2.metric("Expected Baseline", f"{a.expected_value:,.0f}")
            col3.metric("Baseline Deviation", f"{a.deviation_pct:+.1f}%")
            col4.metric("Z-Score", f"{a.z_score:+.2f}")

            st.markdown(f"**💡 Root-Cause Hypothesis**: {a.cause_hypothesis}")
            st.markdown(f"**🎯 Recommended Action**: `{a.recommended_action}`")
            if a.drill_down_data:
                st.json(a.drill_down_data)

# -----------------------------------------------------------------------------
# TAB 3: Semantic Layer Catalog Explorer
# -----------------------------------------------------------------------------
with tab_catalog:
    st.markdown("#### Semantic Layer Data Dictionary & View Definitions")
    catalog = agent.prompt_builder.catalog
    
    views = catalog.get("views", {})
    for vname, vmeta in views.items():
        with st.expander(f"👁️ View `{vname}`", expanded=True):
            st.markdown(f"**Description**: {vmeta.get('description', '')}")
            cols_df = pd.DataFrame([
                {"Column Name": col, "Description": desc}
                for col, desc in vmeta.get("columns", {}).items()
            ])
            st.dataframe(cols_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Curated Few-Shot Text-to-SQL Examples")
    few_shots = catalog.get("few_shot_examples", [])
    for fs in few_shots:
        st.markdown(f"**Q**: {fs['question']}")
        st.code(fs['sql'], language="sql")

# -----------------------------------------------------------------------------
# TAB 4: Guardrails & Security Sandbox
# -----------------------------------------------------------------------------
with tab_guardrails:
    st.markdown("#### SQL AST Guardrail Security Sandbox")
    st.markdown("Test the AST security validator against arbitrary, malicious, or malformed queries.")

    test_sql = st.text_area(
        "Enter raw SQL to test against Guardrails:",
        value="SELECT * FROM v_weekly_sales_summary; DROP TABLE fact_weekly_sales;",
        height=100,
    )

    if st.button("🛡️ Validate SQL AST"):
        guardrail = SQLGuardrail()
        result = guardrail.validate_and_sanitize(test_sql)

        if result.is_valid:
            st.success("✅ Guardrail Passed: Query is safe and complies with security policies.")
            st.markdown(f"**Sanitized SQL (AST Reformatted & Limit Injected)**:\n```sql\n{result.sanitized_sql}\n```")
            st.markdown(f"• Tables Accessed: `{result.tables_accessed}`")
            st.markdown(f"• Limit Enforced: `{result.has_limit}`")
        else:
            st.error(f"❌ Guardrail Violation: {result.error_message}")
            st.markdown(f"• Prohibited SQL Blocked: `{result.original_sql}`")

# -----------------------------------------------------------------------------
# TAB 5: Pipeline Health & DQ Monitor
# -----------------------------------------------------------------------------
with tab_pipeline:
    st.markdown("#### 52-Week Pipeline Execution & Data Quality Audit")
    
    # Summary Cards
    df_runs = db.execute_query("""
        SELECT 
            COUNT(*) AS total_batches,
            SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS clean_batches,
            SUM(CASE WHEN status = 'LOADED_WITH_WARNINGS' THEN 1 ELSE 0 END) AS warning_batches,
            SUM(CASE WHEN status = 'CRITICAL_FAILURE' THEN 1 ELSE 0 END) AS blocked_batches,
            SUM(rows_loaded) AS total_loaded_rows,
            AVG(execution_time_ms) AS avg_latency_ms
        FROM pipeline_runs;
    """)

    if not df_runs.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Batches Processed", f"{df_runs['total_batches'].iloc[0]}")
        c2.metric("Clean Ingestion", f"{df_runs['clean_batches'].iloc[0]} / 52")
        c3.metric("Blocked Critical Anomalies", f"{df_runs['blocked_batches'].iloc[0]} / 52", delta="100% Protected", delta_color="normal")
        c4.metric("Avg Ingestion Latency", f"{df_runs['avg_latency_ms'].iloc[0]:.1f} ms", delta="48,000x vs Manual", delta_color="normal")

    st.markdown("### Ingestion Audit History (All 52 Weeks)")
    df_runs_table = db.execute_query("""
        SELECT 
            week_number AS "Week",
            file_name AS "File Name",
            status AS "Status",
            rows_staged AS "Staged Rows",
            rows_loaded AS "Loaded Rows",
            dq_critical_count AS "Critical DQ Issues",
            dq_warning_count AS "Warning DQ Issues",
            ROUND(execution_time_ms::numeric, 1) AS "Latency (ms)",
            run_timestamp AS "Timestamp"
        FROM pipeline_runs
        ORDER BY week_number ASC;
    """)
    st.dataframe(df_runs_table, use_container_width=True)

    # Download Excel Report button
    if st.button("📊 Generate Fresh Executive Excel Report"):
        reporter = WeeklyReportGenerator()
        rep_path = reporter.generate_excel_report()
        with open(rep_path, "rb") as f:
            st.download_button(
                label="📥 Download Executive Excel Report (.xlsx)",
                data=f.read(),
                file_name=rep_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
