"""
unsql/engines/aurora.py
------------------------
Amazon Aurora adapter.

Aurora supports two compatible modes:
  - MySQL-compatible  (default, port 3306)
  - PostgreSQL-compatible  (port 5432)

The mode is detected from the connection params or defaults to MySQL.
Reuses the MySQL and PostgreSQL adapters under the hood.
"""
from __future__ import annotations

import socket
from typing import Any

from .base import ColumnInfo, DBEngine, QueryResult, TableSchema


class AuroraEngine(DBEngine):
    """
    Adapter for Amazon Aurora.

    Delegates to MySQLEngine or PostgresEngine based on the dialect specified
    at connection time (kwargs['dialect'] = 'mysql' | 'postgresql').
    Defaults to MySQL-compatible.
    """

    def __init__(self) -> None:
        self._delegate: DBEngine | None = None
        self._dialect: str = "mysql"

    @property
    def engine_name(self) -> str:
        return "Amazon Aurora"

    @property
    def engine_type(self) -> str:
        return "aurora"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        dialect_raw = kwargs.pop("dialect", "mysql")
        dialect = (dialect_raw or "mysql").lower()
        self._dialect = dialect

        if dialect == "postgresql":
            from .postgres import PostgresEngine
            self._delegate = PostgresEngine()
        else:
            from .mysql import MySQLEngine
            self._delegate = MySQLEngine()

        # Aurora requires a hostname
        host = kwargs.get("host")
        if not host:
            raise ConnectionError(
                "Aurora requires a hostname (your cluster endpoint). "
                "Use: connect aurora  and provide the endpoint when prompted."
            )
        self._delegate.connect(username, password, **kwargs)

    def disconnect(self) -> None:
        if self._delegate:
            self._delegate.disconnect()
        self._delegate = None

    def is_connected(self) -> bool:
        return self._delegate is not None and self._delegate.is_connected()

    def execute(self, sql: str) -> QueryResult | None:
        if not self._delegate:
            raise RuntimeError("Not connected to Aurora.")
        return self._delegate.execute(sql)

    def commit(self) -> None:
        if self._delegate:
            self._delegate.commit()

    def rollback(self) -> None:
        if self._delegate:
            self._delegate.rollback()

    def get_schema(self) -> list[TableSchema]:
        if not self._delegate:
            return []
        return self._delegate.get_schema()


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
        # Aurora is cloud-hosted; no local discovery
        return [
            "Aurora is a cloud service — provide your cluster endpoint when prompted.",
            "MySQL-compatible default (pass dialect=postgresql for PostgreSQL mode)",
        ]
