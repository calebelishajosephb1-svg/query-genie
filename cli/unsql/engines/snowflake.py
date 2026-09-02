"""
unsql/engines/snowflake.py
---------------------------
Snowflake engine adapter using snowflake-connector-python.

Snowflake is cloud-only — no local instance to discover.
Connection requires an account identifier (e.g. myorg-myaccount).
Credentials prompts are handled by commands.py (_prompt_snowflake_params).
"""
from __future__ import annotations

import re
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import snowflake.connector
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class SnowflakeEngine(DBEngine):
    """Adapter for Snowflake."""

    def __init__(self) -> None:
        self._conn = None
        self._cursor = None

    @property
    def engine_name(self) -> str:
        return "Snowflake"

    @property
    def engine_type(self) -> str:
        return "snowflake"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError(
                "Snowflake connector not installed. "
                "Run: pip install snowflake-connector-python"
            )
        account = kwargs.get("account")
        if not account:
            raise ConnectionError(
                "Snowflake requires an account identifier. "
                "Use: connect snowflake  and provide your account when prompted."
            )
        try:
            self._conn = snowflake.connector.connect(
                user=username,
                password=password,
                account=account,
                warehouse=kwargs.get("warehouse") or None,
                database=kwargs.get("database") or None,
                schema=kwargs.get("schema") or "PUBLIC",
                login_timeout=15,
            )
            self._cursor = self._conn.cursor()
        except Exception as exc:
            raise ConnectionError(f"Snowflake connection failed: {exc}") from exc

    def disconnect(self) -> None:
        for obj in (self._cursor, self._conn):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        self._conn = None
        self._cursor = None

    # ── Capabilities ───────────────────────────────────────────────────────────

    @property
    def ddl_auto_commits(self) -> bool:
        # DDL in this engine implicitly commits the transaction.
        return True

    def is_connected(self) -> bool:
        return self._conn is not None

    def execute(self, sql: str) -> QueryResult | None:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        desc_match = re.match(r"^(?:DESCRIBE\s+TABLE|DESCRIBE|DESC)\s+(\S+)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1))

        try:
            self._cursor.execute(sql)
            if self._cursor.description:
                cols = [d[0] for d in self._cursor.description]
                rows = self._cursor.fetchall()
                return QueryResult(columns=cols, rows=[tuple(r) for r in rows],
                                   rowcount=len(rows), is_select=True)
            return QueryResult(columns=[], rows=[], rowcount=self._cursor.rowcount or 0, is_select=False)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    def _describe(self, table: str) -> QueryResult:
        try:
            table_esc = table.replace('"', '""')
            self._cursor.execute(f'DESCRIBE TABLE "{table_esc}"')
            if self._cursor.description:
                cols = [d[0] for d in self._cursor.description]
                rows = [tuple(r) for r in self._cursor.fetchall()]
                return QueryResult(columns=cols, rows=rows, rowcount=len(rows),
                                   is_select=True, is_describe=True)
            return QueryResult(columns=["Name", "Null?", "Type"], rows=[], rowcount=0,
                               is_select=True, is_describe=True)
        except Exception as exc:
            raise RuntimeError(f"DESCRIBE failed for {table}: {exc}") from exc

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            try:
                self._conn.rollback()
            except Exception:
                pass

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []
        tables: dict[str, TableSchema] = {}
        # NOTE: exceptions intentionally PROPAGATE. Callers distinguish
        # "fetch failed" (None/exception → unknown state) from "confirmed
        # empty" ([]) — swallowing errors here would make a transient
        # network hiccup look like an empty database and trigger the
        # empty-schema short-circuits downstream.
        self._cursor.execute("SHOW TABLES")
        table_rows = self._cursor.fetchall()
        # SHOW TABLES columns vary; name is typically column 1 (index 1)
        for row in table_rows:
            row = tuple(row)
            tname = row[1] if len(row) > 1 else row[0]
            tables[str(tname)] = TableSchema(name=str(tname), columns=[])

        for tname in list(tables.keys()):
            tname_esc = tname.replace('"', '""')
            self._cursor.execute(f'DESCRIBE TABLE "{tname_esc}"')
            col_info = self._cursor.fetchall()
            # Snowflake DESCRIBE TABLE: name, type, kind, null?, default, ...
            for r in col_info:
                r = tuple(r)
                cname = r[0]
                ctype = r[1]
                null_str = r[3] if len(r) > 3 else "Y"
                nullable = "Y" in str(null_str).upper()
                tables[tname].columns.append(ColumnInfo(
                    name=str(cname), type=str(ctype), nullable=nullable
                ))
        return list(tables.values())

    def supports_savepoints(self) -> bool:
        # Snowflake does not support transactions or savepoints.
        return False


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        escaped = ident.replace(chr(34), chr(34)+chr(34))
        return chr(34) + escaped + chr(34)

    def parameter_style(self) -> str:
        return "pyformat"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        return feat in {"cte", "window_functions", "returning", "identity", "generated_columns", "check_constraints", "savepoint"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        parts = []
        if limit is not None:
            parts.append(f"LIMIT {limit}")
        if offset is not None:
            parts.append(f"OFFSET {offset}")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": "RETURNING"}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": True, "ddl_transactional": True, "isolation_levels": ["READ_COMMITTED", "REPEATABLE_READ", "SERIALIZABLE"]}


    def discover(self) -> list[str]:
        # Snowflake is cloud-based; no local discovery possible
        return ["Snowflake is cloud-based — provide your account identifier when prompted"]
