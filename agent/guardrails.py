"""SQL AST Guardrail — Phase 1 hardened implementation.

Changes vs original:
  1a. ViolationType enum; SQLGuardrailResult.violation_type field.
  1b. CTE aliases collected from exp.With and exempted from the allowlist;
      CTE bodies are still validated.
  1c. Top-level LIMIT only (ast.args.get); fail-closed on non-literal values.
  1d. Schema qualifier checked; non-public/non-empty schemas rejected.
  1e. exp.Into (SELECT INTO), locking clauses, COPY, and a function denylist added.
  1f. Keyword pre-screen removed; AST is the single source of truth.
      Max query-length and AST node-count guards added.
  1g. Production allowlist = exactly the 4 semantic views.
  1h. sanitized_sql is always ast.sql(dialect), never the raw model string.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Set

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)


# ── Policy constants ────────────────────────────────────────────────────────

#: The ONLY objects the read-only agent role may query.
SEMANTIC_VIEWS: FrozenSet[str] = frozenset(
    {
        "v_weekly_sales_summary",
        "v_territory_quota_attainment",
        "v_hcp_prescribing_trends",
        "v_product_performance",
    }
)

_ALLOWED_SCHEMAS: FrozenSet[str] = frozenset({"public", ""})

_FORBIDDEN_SYSTEM_SCHEMAS: FrozenSet[str] = frozenset(
    {
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "pg_temp",
        "pg_toast_temp_1",
    }
)

_FORBIDDEN_FUNCTIONS: FrozenSet[str] = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_ls_waldir",
        "pg_ls_logdir",
        "pg_ls_tmpdir",
        "lo_import",
        "lo_export",
        "lo_create",
        "lo_unlink",
        "dblink",
        "dblink_connect",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_close",
        "set_config",
        "current_setting",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_drop_replication_slot",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
    }
)

MAX_QUERY_LENGTH: int = 8_000   # characters
MAX_AST_NODES: int = 500        # total AST nodes


# ── ViolationType enum (1a) ─────────────────────────────────────────────────

class ViolationType(enum.Enum):
    SECURITY = "security"  # stop immediately; is_blocked_security=True in the agent
    SYNTAX   = "syntax"    # parse failure; eligible for repair retry
    POLICY   = "policy"    # unknown column etc; eligible for repair retry
    EMPTY    = "empty"     # blank input


# ── Result type (1a) ────────────────────────────────────────────────────────

@dataclass
class SQLGuardrailResult:
    is_valid: bool
    sanitized_sql: Optional[str] = None
    error_message: Optional[str] = None
    violation_type: Optional[ViolationType] = None
    tables_accessed: List[str] = field(default_factory=list)
    has_limit: bool = False
    original_sql: str = ""

    @property
    def is_security_violation(self) -> bool:
        return self.violation_type is ViolationType.SECURITY

    @property
    def is_retryable(self) -> bool:
        return self.violation_type in (ViolationType.SYNTAX, ViolationType.POLICY)


# ── Helper constructors ─────────────────────────────────────────────────────

def _security(
    msg: str, raw: str, tables: Optional[List[str]] = None
) -> SQLGuardrailResult:
    return SQLGuardrailResult(
        is_valid=False,
        error_message=f"Security Violation: {msg}",
        violation_type=ViolationType.SECURITY,
        tables_accessed=list(tables or []),
        original_sql=raw,
    )


def _syntax(msg: str, raw: str) -> SQLGuardrailResult:
    return SQLGuardrailResult(
        is_valid=False,
        error_message=f"Syntax Error: {msg}",
        violation_type=ViolationType.SYNTAX,
        original_sql=raw,
    )


def _empty_result(raw: str) -> SQLGuardrailResult:
    return SQLGuardrailResult(
        is_valid=False,
        error_message="Query is empty.",
        violation_type=ViolationType.EMPTY,
        original_sql=raw,
    )


# ── Guardrail class ─────────────────────────────────────────────────────────

class SQLGuardrail:
    """Parses, validates, and sanitizes a SQL string before warehouse execution.

    allowed_tables:
        Injects a custom set; useful only for unit tests.
        Production (allowed_tables=None) uses SEMANTIC_VIEWS (the 4 views).
    """

    def __init__(
        self,
        allowed_tables: Optional[Set[str]] = None,
        default_limit: int = 100,
        max_limit: int = 500,
        dialect: str = "postgres",
    ) -> None:
        self.allowed_tables: FrozenSet[str] = (
            frozenset(t.lower() for t in allowed_tables)
            if allowed_tables is not None
            else SEMANTIC_VIEWS
        )
        self.default_limit = default_limit
        self.max_limit = max_limit
        self.dialect = dialect

    # ── public API ──────────────────────────────────────────────────────────

    def validate_and_sanitize(self, raw_sql: str) -> SQLGuardrailResult:  # noqa: C901 (complexity OK — each check is a distinct security layer)
        """Parse → validate → sanitize.  Returns a typed SQLGuardrailResult."""

        # 0. Empty ────────────────────────────────────────────────────────
        if not raw_sql or not raw_sql.strip():
            return _empty_result(raw_sql or "")

        # 0b. Length guard ────────────────────────────────────────────────
        if len(raw_sql) > MAX_QUERY_LENGTH:
            return _security(
                f"Query length {len(raw_sql)} exceeds maximum {MAX_QUERY_LENGTH} characters.",
                raw_sql,
            )

        clean = raw_sql.strip().rstrip(";")

        # 1. Parse AST ────────────────────────────────────────────────────
        try:
            stmts = sqlglot.parse(clean, read=self.dialect)
        except Exception as exc:
            return _syntax(f"Cannot parse: {exc}", raw_sql)

        if not stmts or stmts[0] is None:
            return _empty_result(raw_sql)

        # 2. Single statement  (stacked injection → SECURITY) ─────────────
        if len(stmts) > 1:
            return _security(
                "Multiple SQL statements detected. Only a single SELECT is permitted.",
                raw_sql,
            )

        ast = stmts[0]

        # 3. AST node-count guard ─────────────────────────────────────────
        node_count = sum(1 for _ in ast.walk())
        if node_count > MAX_AST_NODES:
            return _security(
                f"Query AST has {node_count} nodes; maximum is {MAX_AST_NODES}.",
                raw_sql,
            )

        # 4. Top-level must be SELECT / set-op ────────────────────────────
        if not isinstance(ast, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            return _security(
                f"Only SELECT queries are permitted. Got {type(ast).__name__.upper()}.",
                raw_sql,
            )

        # 5. Forbidden statement types anywhere in the AST ─────────────────
        _FORBIDDEN_STMT_TYPES = (
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
        for node in ast.walk():
            if isinstance(node, _FORBIDDEN_STMT_TYPES):
                return _security(
                    f"Prohibited SQL construct '{type(node).__name__.upper()}' detected.",
                    raw_sql,
                )
            # SELECT INTO (stores target in exp.Into child nodes)
            if isinstance(node, exp.Into):
                return _security("SELECT INTO is not permitted.", raw_sql)

        # Also check top-level Select.into arg (dialect-dependent placement)
        if isinstance(ast, exp.Select) and ast.args.get("into"):
            return _security("SELECT INTO is not permitted.", raw_sql)

        # Locking clauses: FOR UPDATE / FOR SHARE
        if isinstance(ast, exp.Select) and ast.args.get("locks"):
            return _security(
                "Locking clause (FOR UPDATE / FOR SHARE) is not permitted.", raw_sql
            )

        # 6. Function denylist ─────────────────────────────────────────────
        forbidden_normalized = {f.replace("_", "").lower() for f in _FORBIDDEN_FUNCTIONS}
        for node in ast.walk():
            names_to_check: Set[str] = set()
            if isinstance(node, (exp.Func, exp.Anonymous)):
                if hasattr(node, "name") and node.name:
                    names_to_check.add(str(node.name).lower())
                if hasattr(node, "this") and node.this:
                    names_to_check.add(str(node.this).lower())
                try:
                    sql_n = node.sql_name()
                    if sql_n:
                        names_to_check.add(sql_n.lower())
                except Exception:
                    pass

            for name in names_to_check:
                clean_name = name.replace("_", "").lower()
                if (
                    clean_name in forbidden_normalized
                    or clean_name.startswith("dblink")
                    or clean_name.startswith("pgls")
                    or clean_name.startswith("pgread")
                    or clean_name.startswith("pgsleep")
                ):
                    return _security(
                        f"Forbidden function '{name}' is not permitted.", raw_sql
                    )

        # 7. Collect CTE aliases so they don't fail the allowlist ──────────
        cte_aliases: Set[str] = set()
        for cte_node in ast.find_all(exp.CTE):
            alias = _norm(cte_node.alias_or_name or "")
            if alias:
                cte_aliases.add(alias)

        # 8. Table allowlist + schema qualifier ────────────────────────────
        tables_accessed: List[str] = []
        for tbl in ast.find_all(exp.Table):
            # sqlglot Table attrs: name, db (schema), catalog
            schema = _norm(str(tbl.args.get("db") or ""))
            catalog = _norm(str(tbl.args.get("catalog") or ""))
            tbl_name = _norm(tbl.name or "")

            # If catalog is non-empty and looks like a schema, treat it as one
            effective_schema = catalog if catalog else schema

            # System schema
            if effective_schema in _FORBIDDEN_SYSTEM_SCHEMAS or schema in _FORBIDDEN_SYSTEM_SCHEMAS:
                return _security(
                    f"Access to system schema is not permitted.",
                    raw_sql,
                    tables_accessed,
                )

            # Non-public user schema
            if effective_schema and effective_schema not in _ALLOWED_SCHEMAS:
                return _security(
                    f"Schema '{effective_schema}' is not in the allowed schema list.",
                    raw_sql,
                    tables_accessed,
                )

            # CTE alias — not a real base table
            if tbl_name in cte_aliases:
                continue

            tables_accessed.append(tbl_name)

            if tbl_name not in self.allowed_tables:
                return _security(
                    f"Table/View '{tbl_name}' is not in the authorized semantic layer allowlist.",
                    raw_sql,
                    tables_accessed,
                )

        # 9. LIMIT — top-level only, fail-closed ──────────────────────────
        # ast.args.get("limit") touches ONLY the outermost limit node.
        # A nested subquery's LIMIT lives on its own Select, not here.
        top_limit = ast.args.get("limit")
        if top_limit is not None:
            val_expr = getattr(top_limit, "expression", None)
            try:
                if not isinstance(val_expr, exp.Literal):
                    raise TypeError("non-literal LIMIT")
                current_val = int(val_expr.this)
                if current_val > self.max_limit or current_val <= 0:
                    top_limit.set("expression", exp.Literal.number(self.max_limit))
            except Exception:
                # Fail-closed: any non-integer or oversized LIMIT → cap at max
                top_limit.set("expression", exp.Literal.number(self.max_limit))
            has_limit = True
        else:
            ast = ast.limit(self.default_limit)
            has_limit = True

        # 1h. Always regenerate SQL from AST — never return the raw model string.
        sanitized = ast.sql(dialect=self.dialect)

        return SQLGuardrailResult(
            is_valid=True,
            sanitized_sql=sanitized,
            violation_type=None,
            tables_accessed=tables_accessed,
            has_limit=has_limit,
            original_sql=raw_sql,
        )


# ── Utility ─────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase and strip common quoting characters."""
    return s.lower().strip().strip('"').strip("'").strip("`")
