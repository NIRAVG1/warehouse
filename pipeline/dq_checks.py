"""Data Quality Validation Engine for Weekly Pharma Sales Ingestion."""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "week_start_date",
    "hcp_id",
    "product_id",
    "territory_id",
    "units",
    "net_revenue",
]

PRIMARY_KEY_COLS = ["week_start_date", "hcp_id", "product_id", "territory_id"]


@dataclass
class DQIssue:
    issue_type: str
    detail: str
    severity: str  # 'critical' or 'warning'
    failed_rows_count: int = 0


@dataclass
class DQReport:
    file_name: str
    week_number: int
    is_valid: bool
    total_rows: int
    critical_issues: List[DQIssue] = field(default_factory=list)
    warning_issues: List[DQIssue] = field(default_factory=list)
    metrics: Dict[str, any] = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return len(self.critical_issues) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warning_issues) > 0

    def summary_dict(self) -> Dict[str, any]:
        return {
            "file_name": self.file_name,
            "week_number": self.week_number,
            "is_valid": self.is_valid,
            "total_rows": self.total_rows,
            "critical_count": len(self.critical_issues),
            "warning_count": len(self.warning_issues),
            "critical_issues": [issue.__dict__ for issue in self.critical_issues],
            "warning_issues": [issue.__dict__ for issue in self.warning_issues],
        }


class DataQualityChecker:
    def __init__(
        self,
        valid_territories: Optional[Set[str]] = None,
        valid_products: Optional[Set[str]] = None,
        expected_min_rows: int = 3000,
    ):
        self.valid_territories = valid_territories or set()
        self.valid_products = valid_products or set()
        self.expected_min_rows = expected_min_rows

    def validate_file(
        self,
        file_path: Path,
        week_number: int,
        historical_avg_rows: Optional[float] = None,
    ) -> tuple[Optional[pd.DataFrame], DQReport]:
        """Runs comprehensive DQ checks on an incoming weekly sales file."""
        file_name = file_path.name
        critical_issues: List[DQIssue] = []
        warning_issues: List[DQIssue] = []

        try:
            df = pd.read_csv(file_path)
        except Exception as e:
            critical_issues.append(
                DQIssue(
                    issue_type="file_unreadable",
                    detail=f"Failed to parse CSV file: {e}",
                    severity="critical",
                )
            )
            report = DQReport(
                file_name=file_name,
                week_number=week_number,
                is_valid=False,
                total_rows=0,
                critical_issues=critical_issues,
                warning_issues=[],
            )
            return None, report

        total_rows = len(df)

        # 1. Schema Check (Required Columns)
        missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            critical_issues.append(
                DQIssue(
                    issue_type="missing_column",
                    detail=f"Required columns missing: {', '.join(missing_cols)}",
                    severity="critical",
                    failed_rows_count=total_rows,
                )
            )
            # If critical schema failure, we cannot safely perform subsequent row-level checks
            report = DQReport(
                file_name=file_name,
                week_number=week_number,
                is_valid=False,
                total_rows=total_rows,
                critical_issues=critical_issues,
                warning_issues=[],
            )
            return df, report

        # 2. Null Keys Check
        null_mask = df[PRIMARY_KEY_COLS].isna().any(axis=1)
        null_count = int(null_mask.sum())
        if null_count > 0:
            null_cols = [c for c in PRIMARY_KEY_COLS if df[c].isna().sum() > 0]
            critical_issues.append(
                DQIssue(
                    issue_type="null_keys",
                    detail=f"Found {null_count} rows with null values in mandatory key columns: {null_cols}",
                    severity="critical",
                    failed_rows_count=null_count,
                )
            )

        # 3. Duplicate Rows / Composite Key Check
        duplicate_mask = df.duplicated(subset=PRIMARY_KEY_COLS, keep=False)
        duplicate_count = int(duplicate_mask.sum())
        if duplicate_count > 0:
            critical_issues.append(
                DQIssue(
                    issue_type="duplicate_rows",
                    detail=f"Found {duplicate_count} duplicated records matching primary key ({', '.join(PRIMARY_KEY_COLS)})",
                    severity="critical",
                    failed_rows_count=duplicate_count,
                )
            )

        # 4. Unknown Territories Check
        if self.valid_territories:
            unknown_terr_mask = ~df["territory_id"].isin(self.valid_territories)
            unknown_terr_count = int(unknown_terr_mask.sum())
            if unknown_terr_count > 0:
                unknown_samples = list(df.loc[unknown_terr_mask, "territory_id"].unique()[:5])
                critical_issues.append(
                    DQIssue(
                        issue_type="unknown_territory",
                        detail=f"Found {unknown_terr_count} rows with unknown territory codes: {unknown_samples}",
                        severity="critical",
                        failed_rows_count=unknown_terr_count,
                    )
                )

        # 5. Row Count Drift / Truncation Check
        expected_threshold = (
            historical_avg_rows * 0.65 if historical_avg_rows else self.expected_min_rows
        )
        if total_rows < expected_threshold:
            critical_issues.append(
                DQIssue(
                    issue_type="truncated_file",
                    detail=f"Row count {total_rows:,} is significantly below expected baseline ({int(expected_threshold):,} threshold)",
                    severity="critical",
                    failed_rows_count=total_rows,
                )
            )

        # 6. Negative Units Check (Warning)
        negative_units_mask = df["units"] < 0
        negative_units_count = int(negative_units_mask.sum())
        if negative_units_count > 0:
            warning_issues.append(
                DQIssue(
                    issue_type="negative_units",
                    detail=f"Found {negative_units_count} rows with negative unit sales (potential returns/anomalies)",
                    severity="warning",
                    failed_rows_count=negative_units_count,
                )
            )

        # 7. Freshness & Date Check
        try:
            pd.to_datetime(df["week_start_date"])
        except Exception as e:
            critical_issues.append(
                DQIssue(
                    issue_type="invalid_date_format",
                    detail=f"Invalid date format in week_start_date: {e}",
                    severity="critical",
                )
            )

        is_valid = len(critical_issues) == 0
        report = DQReport(
            file_name=file_name,
            week_number=week_number,
            is_valid=is_valid,
            total_rows=total_rows,
            critical_issues=critical_issues,
            warning_issues=warning_issues,
            metrics={
                "total_rows": total_rows,
                "null_count": null_count,
                "duplicate_count": duplicate_count,
                "negative_units_count": negative_units_count,
            },
        )
        return df, report
