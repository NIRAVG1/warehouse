"""SQL Guardrails & AST Validator using sqlglot for Text-to-SQL security."""
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set
import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_OBJECTS = {
    # Semantic views
    "v_weekly_sales_summary",
    "v_territory_quota_attainment",
    "v_hcp_prescribing_trends",
    "v_product_performance",
    # Dimension & quota tables
    "dim_product",
    "dim_territory",
    "dim_rep",
    "dim_hcp",
    "territory_quotas",
}

FORBIDDEN_EXPRESSIONS = (
    exp.Drop,
    exp.Alter,
    exp.Delete,
    exp.Insert,
    exp.Update,
    exp.Create,
    exp.Command,
    exp.Pragma,
    exp.Grant,
    exp.Revoke,
)


@dataclass
class SQLGuardrailResult:
    is_valid: bool
    sanitized_sql: Optional[str] = None
    error_message: Optional[str] = None
    tables_accessed: List[str] = field(default_factory=list)
    has_limit: bool = False
    original_sql: str = ""


class SQLGuardrail:
    def __init__(
        self,
        allowed_tables: Optional[Set[str]] = None,
        default_limit: int = 100,
        max_limit: int = 500,
        dialect: str = "postgres",
    ):
        self.allowed_tables = {t.lower() for t in (allowed_tables or DEFAULT_ALLOWED_OBJECTS)}
        self.default_limit = default_limit
        self.max_limit = max_limit
        self.dialect = dialect

    def validate_and_sanitize(self, raw_sql: str) -> SQLGuardrailResult:
        """Parses, validates AST safety, checks table allowlists, and enforces LIMIT clauses."""
        if not raw_sql or not raw_sql.strip():
            return SQLGuardrailResult(
                is_valid=False,
                error_message="Query is empty.",
                original_sql=raw_sql,
            )

        clean_sql = raw_sql.strip().rstrip(";")

        # Security pre-screen for prohibited DDL/DML keywords
        upper_sql = f" {clean_sql.upper()} "
        for dangerous_kw in [" DROP ", " DELETE ", " UPDATE ", " INSERT ", " ALTER ", " TRUNCATE ", " EXEC ", " GRANT ", " REVOKE "]:
            if dangerous_kw in upper_sql and not clean_sql.upper().strip().startswith("SELECT"):
                return SQLGuardrailResult(
                    is_valid=False,
                    error_message=f"Security Violation: Prohibited SQL command '{dangerous_kw.strip()}' detected.",
                    original_sql=raw_sql,
                )

        # 1. Parse SQL into AST using sqlglot
        try:
            parsed_expressions = sqlglot.parse(clean_sql, read=self.dialect)
        except Exception as e:
            return SQLGuardrailResult(
                is_valid=False,
                error_message=f"SQL Syntax Error: Unable to parse query AST ({e})",
                original_sql=raw_sql,
            )

        if not parsed_expressions:
            return SQLGuardrailResult(
                is_valid=False,
                error_message="No valid SQL statements found.",
                original_sql=raw_sql,
            )

        # 2. Enforce Single Statement Execution (Prevent SQL injection stacking)
        if len(parsed_expressions) > 1:
            return SQLGuardrailResult(
                is_valid=False,
                error_message="Multiple SQL statements detected. Only a single SELECT query is permitted.",
                original_sql=raw_sql,
            )

        ast = parsed_expressions[0]

        # 3. Enforce SELECT-only / Reject DDL and DML
        if not isinstance(ast, (exp.Select, exp.Union)):
            return SQLGuardrailResult(
                is_valid=False,
                error_message=f"Security Violation: Only SELECT queries are permitted. Found: {ast.key.upper()}",
                original_sql=raw_sql,
            )

        for forbidden in FORBIDDEN_EXPRESSIONS:
            if ast.find(forbidden):
                return SQLGuardrailResult(
                    is_valid=False,
                    error_message=f"Security Violation: Prohibited SQL command '{forbidden.__name__.upper()}' detected.",
                    original_sql=raw_sql,
                )

        # 4. Enforce Table & View Allowlist
        tables_accessed = []
        for table_node in ast.find_all(exp.Table):
            tbl_name = table_node.name.lower()
            tables_accessed.append(tbl_name)
            if tbl_name not in self.allowed_tables:
                return SQLGuardrailResult(
                    is_valid=False,
                    error_message=f"Security Violation: Table/View '{tbl_name}' is not in the authorized semantic layer allowlist.",
                    tables_accessed=tables_accessed,
                    original_sql=raw_sql,
                )

        # 5. Enforce and Clamp LIMIT Clause
        has_limit = False
        limit_node = ast.find(exp.Limit)
        if limit_node:
            has_limit = True
            try:
                current_limit = int(limit_node.expression.this)
                if current_limit > self.max_limit:
                    limit_node.set("expression", exp.Literal.number(self.max_limit))
            except Exception:
                pass
        else:
            # Inject LIMIT clause
            ast = ast.limit(self.default_limit)
            has_limit = True

        sanitized_sql = ast.sql(dialect=self.dialect)

        return SQLGuardrailResult(
            is_valid=True,
            sanitized_sql=sanitized_sql,
            tables_accessed=tables_accessed,
            has_limit=has_limit,
            original_sql=raw_sql,
        )
