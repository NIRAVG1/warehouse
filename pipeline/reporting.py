"""Pharma Commercial Executive Reporting Engine generating multi-tab Excel workbooks."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config.settings import settings
from db.connection import get_db

logger = logging.getLogger(__name__)


class WeeklyReportGenerator:
    def __init__(self):
        self.db = get_db(read_only=True)
        self.reports_dir = settings.REPORTS_DIR
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_excel_report(self, output_path: Optional[Path] = None) -> Path:
        """Queries warehouse semantic views and builds a polished multi-tab Excel workbook."""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.reports_dir / f"pharma_commercial_weekly_report_{timestamp}.xlsx"

        wb = openpyxl.Workbook()
        # Remove default sheet
        wb.remove(wb.active)

        # Style definitions
        header_fill = PatternFill(start_color="1A365D", end_color="1A365D", fill_type="solid")
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        title_font = Font(name="Arial", size=16, bold=True, color="1A365D")
        subtitle_font = Font(name="Arial", size=10, italic=True, color="4A5568")
        bold_font = Font(name="Arial", size=10, bold=True)
        regular_font = Font(name="Arial", size=10)
        kpi_num_font = Font(name="Arial", size=20, bold=True, color="2B6CB0")
        kpi_label_font = Font(name="Arial", size=9, bold=True, color="718096")
        
        thin_border = Border(
            left=Side(style="thin", color="CBD5E0"),
            right=Side(style="thin", color="CBD5E0"),
            top=Side(style="thin", color="CBD5E0"),
            bottom=Side(style="thin", color="CBD5E0"),
        )
        zebra_fill = PatternFill(start_color="F7FAFC", end_color="F7FAFC", fill_type="solid")
        kpi_fill = PatternFill(start_color="EDF2F7", end_color="EDF2F7", fill_type="solid")

        # ------------------------------------------------------------- Tab 1: Executive KPI Dashboard
        ws1 = wb.create_sheet(title="Executive Summary")
        ws1.views.sheetView[0].showGridLines = True

        # Header Title
        ws1["B2"] = "Pharma Commercial Performance & Pipeline Intelligence Report"
        ws1["B2"].font = title_font
        ws1["B3"] = f"Generated on {datetime.now().strftime('%B %d, %Y at %H:%M:%S UTC')} | Automated Data Warehouse Pipeline"
        ws1["B3"].font = subtitle_font

        # Query core aggregates
        df_kpi = self.db.execute_query("""
            SELECT
                COALESCE(SUM(total_revenue), 0) AS total_revenue,
                COALESCE(SUM(total_units), 0) AS total_units,
                COUNT(DISTINCT territory_id) AS active_territories,
                COUNT(DISTINCT product_id) AS active_products
            FROM v_weekly_sales_summary;
        """)
        
        df_hcps = self.db.execute_query("SELECT COUNT(DISTINCT hcp_id) AS total_hcps FROM dim_hcp;")
        df_attain = self.db.execute_query("SELECT ROUND(AVG(quota_attainment_pct), 1) AS avg_attainment FROM v_territory_quota_attainment;")
        df_runs = self.db.execute_query("SELECT COUNT(*) AS total_runs, SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) AS successful_runs FROM pipeline_runs;")

        tot_rev = float(df_kpi["total_revenue"].iloc[0]) if not df_kpi.empty else 0.0
        tot_units = int(df_kpi["total_units"].iloc[0]) if not df_kpi.empty else 0
        tot_hcps = int(df_hcps["total_hcps"].iloc[0]) if not df_hcps.empty else 0
        avg_attain = float(df_attain["avg_attainment"].iloc[0]) if not df_attain.empty and pd.notna(df_attain["avg_attainment"].iloc[0]) else 0.0
        tot_runs = int(df_runs["total_runs"].iloc[0]) if not df_runs.empty else 0
        succ_runs = int(df_runs["successful_runs"].iloc[0]) if not df_runs.empty else 0

        # KPI Cards (Rows 5 to 7)
        kpis = [
            ("Total Gross Revenue", f"${tot_rev:,.2f}", 2),
            ("Total Prescription Units", f"{tot_units:,}", 5),
            ("Active HCP Network", f"{tot_hcps:,}", 8),
            ("Avg Quota Attainment", f"{avg_attain:.1f}%", 11),
            ("Pipeline Ingested Batches", f"{succ_runs}/{tot_runs}", 14),
        ]

        for label, val, start_col in kpis:
            c_label = ws1.cell(row=5, column=start_col, value=label.upper())
            c_label.font = kpi_label_font
            c_label.alignment = Alignment(horizontal="center", vertical="center")
            c_label.fill = kpi_fill
            
            c_val = ws1.cell(row=6, column=start_col, value=val)
            c_val.font = kpi_num_font
            c_val.alignment = Alignment(horizontal="center", vertical="center")
            c_val.fill = kpi_fill

            # Merge 2 columns for card
            ws1.merge_cells(start_row=5, start_column=start_col, end_row=5, end_column=start_col + 2)
            ws1.merge_cells(start_row=6, start_column=start_col, end_row=7, end_column=start_col + 2)
            
            for r in range(5, 8):
                for c in range(start_col, start_col + 3):
                    ws1.cell(row=r, column=c).border = thin_border

        # High-level product summary in tab 1
        ws1["B9"] = "Commercial Product Portfolio Summary"
        ws1["B9"].font = Font(name="Arial", size=12, bold=True, color="1A365D")

        df_prod_summary = self.db.execute_query("""
            SELECT 
                product_name AS "Product Name",
                therapeutic_area AS "Therapeutic Area",
                unit_price AS "Unit Price ($)",
                total_units_sold AS "Units Sold",
                total_gross_revenue AS "Gross Revenue ($)",
                total_prescribing_hcps AS "Prescribing HCPs"
            FROM v_product_performance
            ORDER BY total_gross_revenue DESC;
        """)

        self._render_dataframe_table(ws1, df_prod_summary, start_row=11, start_col=2, header_fill=header_fill, header_font=header_font, border=thin_border, zebra_fill=zebra_fill)

        # ------------------------------------------------------------- Tab 2: Territory Quota Attainment
        ws2 = wb.create_sheet(title="Quota Attainment")
        ws2.views.sheetView[0].showGridLines = True
        ws2["B2"] = "Territory & Rep Quota Attainment Breakdown"
        ws2["B2"].font = title_font

        df_quota = self.db.execute_query("""
            SELECT 
                quarter AS "Quarter",
                territory_id AS "Territory",
                state AS "State",
                rep_name AS "Sales Rep",
                product_name AS "Product",
                therapeutic_area AS "Therapeutic Area",
                actual_units AS "Actual Units",
                quota_units AS "Quota Units",
                quota_attainment_pct AS "Attainment %",
                variance_units AS "Variance (Units)",
                performance_status AS "Status"
            FROM v_territory_quota_attainment
            ORDER BY quarter DESC, quota_attainment_pct DESC;
        """)
        self._render_dataframe_table(ws2, df_quota, start_row=4, start_col=2, header_fill=header_fill, header_font=header_font, border=thin_border, zebra_fill=zebra_fill)

        # ------------------------------------------------------------- Tab 3: HCP Prescribing Trends
        ws3 = wb.create_sheet(title="Top Prescribers")
        ws3.views.sheetView[0].showGridLines = True
        ws3["B2"] = "Top Healthcare Provider (HCP) Prescribing Volume"
        ws3["B2"].font = title_font

        df_hcp_top = self.db.execute_query("""
            SELECT 
                hcp_id AS "HCP ID",
                full_name AS "Provider Name",
                specialty AS "Specialty",
                state AS "State",
                segment AS "Decile Segment",
                product_name AS "Product",
                total_prescribed_units AS "Prescribed Units",
                total_prescribed_revenue AS "Net Revenue ($)",
                avg_weekly_units AS "Avg Weekly Units",
                active_weeks AS "Active Weeks"
            FROM v_hcp_prescribing_trends
            ORDER BY total_prescribed_revenue DESC
            LIMIT 50;
        """)
        self._render_dataframe_table(ws3, df_hcp_top, start_row=4, start_col=2, header_fill=header_fill, header_font=header_font, border=thin_border, zebra_fill=zebra_fill)

        # ------------------------------------------------------------- Tab 4: Pipeline Data Quality Audit
        ws4 = wb.create_sheet(title="DQ Pipeline Audit")
        ws4.views.sheetView[0].showGridLines = True
        ws4["B2"] = "Automated Data Quality Checks & Pipeline Run History"
        ws4["B2"].font = title_font

        df_audit = self.db.execute_query("""
            SELECT 
                run_id AS "Run ID",
                file_name AS "Incoming Batch File",
                week_number AS "Week",
                status AS "Pipeline Status",
                rows_staged AS "Rows Staged",
                rows_loaded AS "Rows Loaded",
                dq_critical_count AS "Critical DQ Issues",
                dq_warning_count AS "Warning DQ Issues",
                ROUND(execution_time_ms::numeric, 1) AS "Execution Time (ms)",
                run_timestamp AS "Timestamp"
            FROM pipeline_runs
            ORDER BY week_number ASC;
        """)
        self._render_dataframe_table(ws4, df_audit, start_row=4, start_col=2, header_fill=header_fill, header_font=header_font, border=thin_border, zebra_fill=zebra_fill)

        wb.save(output_path)
        logger.info(f"Generated executive Excel report at: {output_path}")
        return output_path

    def _render_dataframe_table(
        self,
        ws,
        df: pd.DataFrame,
        start_row: int,
        start_col: int,
        header_fill,
        header_font,
        border,
        zebra_fill,
    ):
        """Helper to render styled tabular data into an openpyxl worksheet."""
        if df.empty:
            ws.cell(row=start_row, column=start_col, value="No records found.")
            return

        # Render Header
        for col_idx, col_name in enumerate(df.columns, start=start_col):
            cell = ws.cell(row=start_row, column=col_idx, value=str(col_name))
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border

        # Render Rows
        for row_idx, row_data in enumerate(df.itertuples(index=False), start=start_row + 1):
            is_even = (row_idx % 2 == 0)
            for col_idx, val in enumerate(row_data, start=start_col):
                # Format numbers
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = Font(name="Arial", size=10)
                cell.border = border
                if is_even:
                    cell.fill = zebra_fill

                if isinstance(val, (int, float)):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

        # Auto-adjust column widths
        for col_idx in range(start_col, start_col + len(df.columns)):
            col_letter = get_column_letter(col_idx)
            max_len = max(len(str(ws.cell(row=r, column=col_idx).value or '')) for r in range(start_row, start_row + len(df) + 1))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)
