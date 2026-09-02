"""
unsql/engines/access.py
------------------------
Microsoft Access adapter using pyodbc with the ACE ODBC driver.

Requires: Microsoft Access Database Engine (ACE) ODBC driver.
  - 32-bit or 64-bit depending on Python bitness.
  - Download: https://www.microsoft.com/en-us/download/details.aspx?id=54920

Discovers .accdb and .mdb files in the current directory and home directory.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema

try:
    import pyodbc
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_ACE_DRIVERS = [
    "Microsoft Access Driver (*.mdb, *.accdb)",
    "Microsoft Access Driver (*.mdb)",
]


def _find_ace_driver() -> str | None:
    if not _AVAILABLE:
        return None
    available = pyodbc.drivers()
    for drv in _ACE_DRIVERS:
        if drv in available:
            return drv
    return None


class AccessEngine(DBEngine):
    """Adapter for Microsoft Access (.accdb / .mdb)."""

    def __init__(self) -> None:
        self._conn = None
        self._cursor = None
        self._file_path: str = ""

    @property
    def engine_name(self) -> str:
        return "Microsoft Access"

    @property
    def engine_type(self) -> str:
        return "access"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        if not _AVAILABLE:
            raise RuntimeError("pyodbc not installed. Run: pip install pyodbc")
        driver = _find_ace_driver()
        if not driver:
            raise RuntimeError(
                "Microsoft Access (ACE) ODBC driver not found. "
                "Install 'Microsoft Access Database Engine' from Microsoft."
            )
        file_path = kwargs.get("file_path")
        if not file_path:
            raise ConnectionError("No Access file path specified.")

        self._file_path = str(file_path)
        # Use odbc_quote for PWD to prevent injection of ;Trusted_Connection etc (M-2)
        from .mssql import odbc_quote
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"DBQ={self._file_path};"
        )
        if password:
            conn_str += f"PWD={odbc_quote(password)};"
        try:
            self._conn = pyodbc.connect(conn_str, timeout=10)
            self._conn.autocommit = False
            self._cursor = self._conn.cursor()
        except Exception as exc:
            raise ConnectionError(f"Access connection failed: {exc}") from exc

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
        # Access SQL uses # for date literals and & for string concat
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
                rows = [tuple(r) for r in self._cursor.fetchall()]
                return QueryResult(columns=cols, rows=rows, rowcount=len(rows), is_select=True)
            return QueryResult(columns=[], rows=[], rowcount=self._cursor.rowcount or 0, is_select=False)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    def _describe(self, table: str) -> QueryResult:
        """Emulate DESCRIBE using pyodbc's columns() catalog function."""
        try:
            cols_meta = self._cursor.columns(table=table)
            rows = []
            for row in cols_meta.fetchall():
                # pyodbc columns(): table_cat, table_schem, table_name,
                # column_name, data_type, type_name, column_size, ...
                # is_nullable is at index 17 (SQL_NULLABLE_UNKNOWN=2)
                cname = row[3]
                type_name = row[5]
                col_size = row[6]
                nullable_code = row[17] if len(row) > 17 else 1
                null_str = "" if nullable_code != 0 else "NOT NULL"
                type_str = f"{type_name}({col_size})" if col_size else type_name
                rows.append((cname, null_str, type_str))
            return QueryResult(
                columns=["Name", "Null?", "Type"],
                rows=rows, rowcount=len(rows),
                is_select=True, is_describe=True,
            )
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
        # NOTE: exceptions intentionally PROPAGATE so callers can distinguish
        # "fetch failed" (unknown state) from "confirmed empty" ([]).
        for row in self._cursor.tables(tableType="TABLE").fetchall():
            tname = row.table_name
            tables[tname] = TableSchema(name=tname, columns=[])
        for tname, ts in list(tables.items()):
            for row in self._cursor.columns(table=tname).fetchall():
                cname = row[3]
                type_name = row[5]
                col_size = row[6]
                nullable_code = row[17] if len(row) > 17 else 1
                ts.columns.append(ColumnInfo(
                    name=cname,
                    type=f"{type_name}({col_size})" if col_size else type_name,
                    nullable=(nullable_code != 0),
                ))
        return list(tables.values())

    def supports_savepoints(self) -> bool:
        # Access/JET has no transactional savepoints.
        return False


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
        # JET/ACE has no CTE, window functions or savepoints.
        return feat in {"check_constraints", "identity"}

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
        return {"supports_savepoint": False, "ddl_transactional": False, "isolation_levels": ["READ_COMMITTED", "SNAPSHOT"]}


    def discover(self) -> list[str]:
        hints: list[str] = []
        search_dirs = [Path.cwd(), Path.home()]
        extensions = {".accdb", ".mdb"}
        seen: set[str] = set()
        for d in search_dirs:
            try:
                for f in sorted(d.iterdir()):
                    if f.is_file() and f.suffix.lower() in extensions:
                        fp = str(f.resolve())
                        if fp not in seen:
                            hints.append(fp)
                            seen.add(fp)
            except PermissionError:
                pass
        return hints
