"""Notification and alert dispatcher for pipeline failures and warnings."""
import json
import logging
from typing import Any, Dict, Optional
import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(self, slack_webhook_url: Optional[str] = None):
        self.slack_webhook_url = slack_webhook_url or settings.SLACK_WEBHOOK_URL
        self.recipient_email = settings.ALERT_EMAIL_RECIPIENT

    def send_critical_failure_alert(
        self,
        file_name: str,
        week_number: int,
        issues: list[dict[str, Any]],
        execution_time_ms: float,
    ) -> bool:
        """Dispatches an alert when a weekly ingestion file fails critical DQ checks."""
        subject = f"🚨 [CRITICAL ALERT] Pharma DW Ingestion Blocked: Week {week_number} ({file_name})"
        issue_details = "\n".join(
            [f"• *{i.get('issue_type')}*: {i.get('detail')} ({i.get('failed_rows_count', 0)} rows)" for i in issues]
        )
        message = (
            f"*Pipeline Run Blocked by Critical Data Quality Failure*\n"
            f"• *File*: `{file_name}` (Week {week_number})\n"
            f"• *Execution Time*: {execution_time_ms:.1f}ms\n"
            f"• *Issues Detected*:\n{issue_details}\n"
            f"• *Action Taken*: Ingestion aborted. Staging & fact tables protected."
        )

        logger.error(f"[ALERT] {subject}\n{message}")
        return self._send_slack_or_log(message, level="critical")

    def send_warning_alert(
        self,
        file_name: str,
        week_number: int,
        issues: list[dict[str, Any]],
        rows_loaded: int,
    ) -> bool:
        """Dispatches a warning alert when non-critical anomalies are detected."""
        subject = f"⚠️ [WARNING] Pharma DW Ingestion: Week {week_number} ({file_name})"
        issue_details = "\n".join(
            [f"• *{i.get('issue_type')}*: {i.get('detail')} ({i.get('failed_rows_count', 0)} rows)" for i in issues]
        )
        message = (
            f"*Pipeline Completed with Data Quality Warnings*\n"
            f"• *File*: `{file_name}` (Week {week_number})\n"
            f"• *Rows Loaded*: {rows_loaded:,}\n"
            f"• *Warnings*:\n{issue_details}\n"
            f"• *Action Taken*: Data loaded to warehouse; flagged for audit."
        )

        logger.warning(f"[ALERT] {subject}\n{message}")
        return self._send_slack_or_log(message, level="warning")

    def _send_slack_or_log(self, message: str, level: str = "info") -> bool:
        if self.slack_webhook_url:
            try:
                payload = {"text": message}
                resp = requests.post(
                    self.slack_webhook_url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    timeout=5,
                )
                return resp.status_code == 200
            except Exception as e:
                logger.warning(f"Failed to post alert to Slack webhook: {e}")
                return False
        return True
