"""
unsql/engines/mariadb.py
-------------------------
MariaDB engine adapter — reuses mysql-connector-python (fully compatible).

MariaDB uses the same wire protocol as MySQL, so the same driver works.
Connection defaults to port 3306.
"""
from __future__ import annotations

import socket
from typing import Any

from .mysql import MySQLEngine  # reuse MySQL implementation


class MariaDBEngine(MySQLEngine):
    """
    Adapter for MariaDB.

    Inherits all MySQL logic — MariaDB is wire-compatible with MySQL.
    Only the identity properties differ.
    """

    @property
    def engine_name(self) -> str:
        return "MariaDB"

    @property
    def engine_type(self) -> str:
        return "mariadb"

    def connect(self, username: str, password: str, **kwargs: Any) -> None:
        # Default MariaDB port is 3306 (same as MySQL)
        kwargs["port"] = kwargs.get("port") or 3306
        super().connect(username, password, **kwargs)


    # -- Dialect overrides (Phase 03) ------------------------------------
    def quote_identifier(self, ident: str) -> str:
        if "." in ident:
            return ".".join(self.quote_identifier(p) for p in ident.split("."))
        escaped = ident.replace("`", "``")
        return "`" + escaped + "`"

    def parameter_style(self) -> str:
        return "pyformat"

    def supports_feature(self, feature: str) -> bool:
        feat = feature.lower()
        # MySQL 8 supports CTE/window, but not returning
        return feat in {"cte", "window_functions", "check_constraints", "savepoint", "identity"}

    def pagination_syntax(self, limit=None, offset=None) -> str:
        parts = []
        if limit is not None:
            parts.append(f"LIMIT {limit}")
        if offset is not None:
            parts.append(f"OFFSET {offset}")
        return " ".join(parts)

    def identity_behavior(self) -> dict:
        return {"supports_identity": True, "insert_omit": True, "returning_clause": None}

    def transaction_behavior(self) -> dict:
        # MySQL DDL auto commits
        return {"supports_savepoint": True, "ddl_transactional": False, "isolation_levels": ["READ_COMMITTED", "REPEATABLE_READ"]}


    def discover(self) -> list[str]:
        hints: list[str] = []
        # Check 3306 (shared with MySQL) and 3307 (common alternate MariaDB port)
        for port in (3306, 3307):
            try:
                with socket.create_connection(("localhost", port), timeout=1):
                    hints.append(f"localhost:{port} (port open — may be MariaDB or MySQL)")
            except (socket.timeout, ConnectionRefusedError, OSError):
                pass
        if not hints:
            hints.append("localhost:3306 (default — not detected)")
        return hints
