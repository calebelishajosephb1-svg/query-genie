"""
unsql/engines/mssql.py
-----------------------
Microsoft SQL Server adapter using pyodbc.

Requires the Microsoft ODBC Driver for SQL Server to be installed on the system.
Auto-discovers SQL Server on port 1433 (localhost).
"""
from __future__ import annotations

import re
import socket
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import pyodbc
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

# Try to find best available ODBC driver
_ODBC_DRIVERS = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
]


def _find_odbc_driver() -> str | None:
    if not _AVAILABLE:
        return None
    available = pyodbc.drivers()
    for drv in _ODBC_DRIVERS:
        if drv in available:
            return drv
    return None


def odbc_quote(value: str) -> str:
    """
    Quote a value for an ODBC connection string.

    ODBC connection-string values containing ';', '{', '}', '=' or leading
    spaces must be wrapped in braces, and any literal '}' inside them must
    be doubled. Without this, a password like `p@ss;word` silently truncates
    at the semicolon (and crafted values could inject extra conn-string keys).
    """
    value = str(value)
    if any(ch in value for ch in ";{}=") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


class MSSQLEngine(DBEngine):
    """Adapter for Microsoft SQL Server."""

    def __init__(self) -> None:
        self._conn = None
        self._cursor = None

    @property
    def engine_name(self) -> str:
        return "Microsoft SQL Server"

    @property
    def engine_type(self) -> str:
        return "mssql"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError("pyodbc not installed. Run: pip install pyodbc")
        driver = _find_odbc_driver()
        if not driver:
            raise RuntimeError(
                "No SQL Server ODBC driver found. Install Microsoft ODBC Driver for SQL Server."
            )
        host = kwargs.get("host") or "localhost"
        port_raw = kwargs.get("port")
        port = int(port_raw) if port_raw else 1433
        database = kwargs.get("database") or "master"
        # TLS: honor Encrypt flag if provided, else warn about current default
        encrypt = kwargs.get("encrypt", kwargs.get("Encrypt"))
        trust_cert = kwargs.get("trust_server_certificate", kwargs.get("TrustServerCertificate"))
        encrypt_str = ""
        if encrypt is not None:
            encrypt_str = f"Encrypt={'yes' if str(encrypt).lower() in ('yes','true','1') else 'no'};"
            if trust_cert is not None:
                tc = 'yes' if str(trust_cert).lower() in ('yes','true','1') else 'no'
                encrypt_str += f"TrustServerCertificate={tc};"
            else:
                encrypt_str += "TrustServerCertificate=yes;"
        else:
            # Default in code is TrustServerCertificate=yes (no verification) — warn
            encrypt_str = "TrustServerCertificate=yes;"
            if host not in ("localhost", "127.0.0.1", "::1"):
                print(f"  [Security] SQL Server connection to {host} without explicit Encrypt/TrustServerCertificate — verify TLS settings.")
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={host},{port};"
            f"DATABASE={odbc_quote(database)};"
            f"UID={odbc_quote(username)};"
            f"PWD={odbc_quote(password)};"
            f"{encrypt_str}"
        )
        try:
            self._conn = pyodbc.connect(conn_str, timeout=10)
            self._conn.autocommit = False
            self._cursor = self._conn.cursor()
        except Exception as exc:
            from unsql.core.security.redact import redact as _redact
            safe_msg = _redact(str(exc))
            raise ConnectionError(f"SQL Server connection failed: {safe_msg}") from None

    def disconnect(self) -> None:
        for obj in (self._cursor, self._conn):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        self._conn = None
        self._cursor = None

    def is_connected(self) -> bool:
        return self._conn is not None

    def execute(self, sql: str) -> QueryResult | None:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return None

        desc_match = re.match(r"^(?:DESCRIBE|DESC)\s+(\S+)$", sql, re.IGNORECASE)
        if desc_match:
            return self._describe(desc_match.group(1).strip("[]\"'"))

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
            SELECT COLUMN_NAME AS [Name],
                   CASE WHEN IS_NULLABLE = 'NO' THEN 'NOT NULL' ELSE '' END AS [Null?],
                   DATA_TYPE +
                   CASE WHEN CHARACTER_MAXIMUM_LENGTH IS NOT NULL
                        THEN '(' + CAST(CHARACTER_MAXIMUM_LENGTH AS VARCHAR) + ')'
                        WHEN NUMERIC_PRECISION IS NOT NULL AND NUMERIC_SCALE IS NOT NULL
                        THEN '(' + CAST(NUMERIC_PRECISION AS VARCHAR) + ',' + CAST(NUMERIC_SCALE AS VARCHAR) + ')'
                        ELSE '' END AS [Type]
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
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
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            try:
                self._conn.rollback()
            except Exception:
                pass

    # ── Savepoints (T-SQL syntax: SAVE TRANSACTION / ROLLBACK TRANSACTION) ───

    def savepoint(self, name: str) -> None:
        self.execute(f"SAVE TRANSACTION {name}")

    def rollback_to_savepoint(self, name: str) -> None:
        self.execute(f"ROLLBACK TRANSACTION {name}")

    def release_savepoint(self, name: str) -> None:
        # SQL Server has no RELEASE SAVEPOINT — savepoints live until
        # commit/rollback. Safe no-op.
        pass

    def get_schema(self) -> list[TableSchema]:
        if not self._conn:
            return []

        tables: dict[str, TableSchema] = {}
        self._cursor.execute("""
            SELECT table_name FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY table_name
        """)
        for (tname,) in self._cursor.fetchall():
            tables[tname] = TableSchema(name=tname, columns=[])

        self._cursor.execute("""
            SELECT table_name, column_name, data_type,
                   character_maximum_length, numeric_precision, numeric_scale,
                   is_nullable
            FROM INFORMATION_SCHEMA.COLUMNS
            ORDER BY table_name, ordinal_position
        """)
        for tname, cname, dtype, clen, nprec, nscale, nullable in self._cursor.fetchall():
            if tname not in tables:
                continue
            ts = dtype
            if clen:
                ts = f"{dtype}({clen})"
            elif nprec is not None and nscale is not None:
                ts = f"{dtype}({nprec},{nscale})"
            tables[tname].columns.append(ColumnInfo(
                name=cname, type=ts, nullable=(nullable == "YES")
            ))

        # PKs
        self._cursor.execute("""
            SELECT kcu.table_name, kcu.column_name
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            WHERE tc.constraint_type = 'PRIMARY KEY'
        """)
        for tname, cname in self._cursor.fetchall():
            if tname in tables:
                for col in tables[tname].columns:
                    if col.name == cname:
                        col.primary_key = True
                        col.nullable = False

        # FKs
        self._cursor.execute("""
            SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME,
                   ccu.TABLE_NAME AS ref_table, ccu.COLUMN_NAME AS ref_col
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
            JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
              ON kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
             AND kcu.TABLE_SCHEMA = rc.CONSTRAINT_SCHEMA
            JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
              ON rc.UNIQUE_CONSTRAINT_NAME = ccu.CONSTRAINT_NAME
        """)
        for tname, cname, ref_table, ref_col in self._cursor.fetchall():
            if tname in tables:
                for col in tables[tname].columns:
                    if col.name == cname:
                        col.foreign_key = f"{ref_table}.{ref_col}"

        return list(tables.values())


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        escaped = ident.replace("]", "]]")
        return "[" + escaped + "]"

    def parameter_style(self) -> str:
        return "qmark"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        return feat in {"cte", "window_functions", "check_constraints", "savepoint", "identity"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        # SQL Server 2012+: OFFSET x ROWS FETCH NEXT y ROWS ONLY, or TOP
        if offset is not None and limit is not None:
            return f"OFFSET {offset} ROWS FETCH NEXT {limit} ROWS ONLY"
        if offset is not None:
            return f"OFFSET {offset} ROWS"
        if limit is not None:
            return f"TOP {limit}"
        return ""

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": "OUTPUT INSERTED.*"}

    def transaction_behavior(self) -> dict:
        return {"supports_savepoint": True, "ddl_transactional": False, "isolation_levels": ["READ_COMMITTED", "SNAPSHOT"]}


    def discover(self) -> list[str]:
        hints: list[str] = []
        try:
            with socket.create_connection(("localhost", 1433), timeout=1):
                hints.append("localhost:1433 (SQL Server port open)")
        except (socket.timeout, ConnectionRefusedError, OSError):
            hints.append("localhost:1433 (default — not detected)")
        return hints
