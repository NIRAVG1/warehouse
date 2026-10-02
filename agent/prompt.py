"""Prompt builder and semantic context provider for the Text-to-SQL Agent."""
from pathlib import Path
from typing import Any, Dict, List
import yaml

from config.settings import settings

CATALOG_PATH = settings.BASE_DIR / "config" / "semantic_catalog.yaml"


class SemanticCatalogPromptBuilder:
    def __init__(self, catalog_path: Path = CATALOG_PATH):
        self.catalog_path = catalog_path
        self.catalog = self._load_catalog()

    def _load_catalog(self) -> Dict[str, Any]:
        if not self.catalog_path.exists():
            return {}
        with open(self.catalog_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def build_system_prompt(self) -> str:
        """Constructs the system prompt containing semantic layer views, columns, and few-shot examples."""
        views = self.catalog.get("views", {})
        few_shots = self.catalog.get("few_shot_examples", [])

        schema_lines = ["### SEMANTIC LAYER SCHEMA (PostgreSQL Dialect):"]
        for vname, vmeta in views.items():
            schema_lines.append(f"\nVIEW `{vname}`: {vmeta.get('description', '')}")
            schema_lines.append("Columns:")
            for col, desc in vmeta.get("columns", {}).items():
                schema_lines.append(f"  - `{col}`: {desc}")

        few_shot_lines = ["\n### FEW-SHOT EXAMPLES:"]
        for ex in few_shots:
            few_shot_lines.append(f"Question: {ex['question']}")
            few_shot_lines.append(f"SQL:\n```sql\n{ex['sql'].strip()}\n```\n")

        prompt = f"""You are Antigravity's Senior Pharma Commercial Data Analyst AI.
Your role is to translate business questions from commercial sales leaders, brand managers, and sales reps into precise, high-performance PostgreSQL queries using the warehouse semantic views.

{chr(10).join(schema_lines)}

### RULES & CONSTRAINTS:
1. ONLY write SELECT queries. NEVER write INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or DDL statements.
2. ONLY use the semantic views (`v_weekly_sales_summary`, `v_territory_quota_attainment`, `v_hcp_prescribing_trends`, `v_product_performance`) unless explicitly instructed otherwise.
3. For quarterly calculations, use the format '2025Q1', '2025Q2', '2025Q3', '2025Q4'.
4. Products available: CARDIVEX (Cardiovascular), GLUCORA (Diabetes), RESPIRA (Respiratory), NEUROLIN (Neurology), ONCORA (Oncology), DERMACLEAR (Dermatology).
5. Always order results meaningfully (e.g. ORDER BY total_revenue DESC or week_start_date ASC).
6. Always return your SQL query enclosed in a single ```sql ... ``` block.

{chr(10).join(few_shot_lines)}
"""
        return prompt

    def build_repair_prompt(self, question: str, failed_sql: str, error_message: str) -> str:
        """Builds a correction prompt when an initial SQL execution or validation failed."""
        return f"""The previous SQL query you generated produced an error. Please fix the query to address the issue.

User Question: {question}
Failed SQL:
```sql
{failed_sql}
```
Error Details:
{error_message}

Please provide the corrected PostgreSQL query enclosed in a ```sql ... ``` code block.
"""
