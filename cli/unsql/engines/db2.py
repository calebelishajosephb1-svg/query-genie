"""
unsql/engines/db2.py
---------------------
IBM Db2 engine adapter using ibm_db / ibm_db_dbi.

Auto-discovers Db2 on the default port 50000.
"""
from __future__ import annotations

import re
import socket
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import ibm_db
    import ibm_db_dbi
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class Db2Engine(DBEngine):
    """Adapter for IBM Db2."""

    def __init__(self) -> None:
        self._conn = None
        self._dbi_conn = None
        self._cursor = None

    @property
    def engine_name(self) -> str:
        return "IBM Db2"

    @property
    def engine_type(self) -> str:
        return "db2"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError(
                "IBM Db2 driver not installed. Run: pip install ibm_db"
            )
        host = kwargs.get("host") or "localhost"
        port_raw = kwargs.get("port")
        port = int(port_raw) if port_raw else 50000
        database = kwargs.get("database") or "SAMPLE"
        # Quote password for conn-string injection (M-2) — same rules as ODBC
        def _q(v: str) -> str:
            v = str(v)
            if any(ch in v for ch in ";{}=") or v != v.strip():
                return "{" + v.replace("}", "}}") + "}"
            return v
        conn_str = (
            f"DATABASE={database};"
            f"HOSTNAME={host};"
            f"PORT={port};"
            f"PROTOCOL=TCPIP;"
            f"UID={_q(username)};"
            f"PWD={_q(password)};"
        )
        try:
            self._conn = ibm_db.connect(conn_str, "", "")
            self._dbi_conn = ibm_db_dbi.Connection(self._conn)
            self._cursor = self._dbi_conn.cursor()
        except Exception as exc:
            raise ConnectionError(f"IBM Db2 connection failed: {exc}") from exc

    def disconnect(self) -> None:
        if self._cursor:
            try:
                self._cursor.close()
            except Exception:
                pass
        if self._conn:
            try:
                ibm_db.close(self._conn)
            except Exception:
                pass
        self._conn = None
        self._dbi_conn = None
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

        desc_match = re.match(r"^(?:DESCRIBE|DESC)\s+(\S+)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1).upper())

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
        sql = """
            SELECT COLNAME AS "Name",
                   CASE WHEN NULLS = 'N' THEN 'NOT NULL' ELSE '' END AS "Null?",
                   TYPENAME ||
                   CASE WHEN LENGTH > 0 AND TYPENAME IN ('VARCHAR','CHAR','GRAPHIC','VARGRAPHIC')
                        THEN '(' || CAST(LENGTH AS VARCHAR(10)) || ')'
                        WHEN SCALE > 0
                        THEN '(' || CAST(LENGTH AS VARCHAR(10)) || ',' || CAST(SCALE AS VARCHAR(10)) || ')'
                        ELSE ''
                   END AS "Type"
            FROM SYSCAT.COLUMNS
            WHERE TABNAME = ?
            ORDER BY COLNO
        """
        try:
            self._cursor.execute(sql, (table,))
            cols = ["Name", "Null?", "Type"]
            rows = [tuple(r) for r in self._cursor.fetchall()]
            return QueryResult(columns=cols, rows=rows, rowcount=len(rows),
                               is_select=True, is_describe=True)
        except Exception as exc:
            raise RuntimeError(f"DESCRIBE failed for {table}: {exc}") from exc

    def commit(self) -> None:
        if self._dbi_conn:
            self._dbi_conn.commit()

    def rollback(self) -> None:
        if self._dbi_conn:
            try:
                self._dbi_conn.rollback()
            except Exception:
                pass

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []
        tables: dict[str, TableSchema] = {}
        # Use current schema
        self._cursor.execute("SELECT CURRENT SCHEMA FROM SYSIBM.SYSDUMMY1")
        (schema_name,) = self._cursor.fetchone()

        self._cursor.execute(
            "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = ? AND TYPE = 'T' ORDER BY TABNAME",
            (schema_name,)
        )
        for (tname,) in self._cursor.fetchall():
            tables[tname] = TableSchema(name=tname, columns=[])

        self._cursor.execute(
            "SELECT TABNAME, COLNAME, TYPENAME, LENGTH, SCALE, NULLS "
            "FROM SYSCAT.COLUMNS WHERE TABSCHEMA = ? ORDER BY TABNAME, COLNO",
            (schema_name,)
        )
        for tname, cname, dtype, length, scale, nulls in self._cursor.fetchall():
            if tname not in tables:
                continue
            ts = dtype
            if dtype in ("VARCHAR", "CHAR", "GRAPHIC", "VARGRAPHIC") and length:
                ts = f"{dtype}({length})"
            elif scale:
                ts = f"{dtype}({length},{scale})"
            tables[tname].columns.append(ColumnInfo(
                name=cname, type=ts, nullable=(nulls == "Y")
            ))
        return list(tables.values())


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        # Oracle uppercases unquoted, quoted preserves but we upper
        escaped = ident.upper().replace(chr(34), chr(34)+chr(34))
        return chr(34) + escaped + chr(34)

    def parameter_style(self) -> str:
        return "named"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        # Oracle 12c+ supports all
        return feat in {"cte", "window_functions", "returning", "identity", "generated_columns", "check_constraints", "savepoint"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        # Oracle 12c: OFFSET ... ROWS FETCH NEXT ... ROWS ONLY
        parts = []
        if offset is not None:
            parts.append(f"OFFSET {offset} ROWS")
        if limit is not None:
            parts.append(f"FETCH NEXT {limit} ROWS ONLY")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": "RETURNING"}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": True, "ddl_transactional": False, "isolation_levels": ["READ_COMMITTED", "SERIALIZABLE"]}


    def discover(self) -> list[str]:
        hints: list[str] = []
        try:
            with socket.create_connection(("localhost", 50000), timeout=1):
                hints.append("localhost:50000 (Db2 port open)")
        except (socket.timeout, ConnectionRefusedError, OSError):
            hints.append("localhost:50000 (default — not detected)")
        return hints
