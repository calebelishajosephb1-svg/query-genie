"""
unsql/repl.py
-------------
The UNSQL session: a real-time natural-language layer sitting directly on top
of a live database engine.

Syntax rule, no exceptions: every input ends with ';'.
Prompt is CONNECT> until you connect, then UNSQL[<engine>]>.
"""
from __future__ import annotations

import getpass
import socket
import sys
from typing import Any

from .core.termsetup import setup_console

setup_console()

from . import render  # noqa: E402
from .agent import Agent, split_statements, statement_kind  # noqa: E402
from .ai import AIClient, AIError  # noqa: E402

try:
    from .gui.visualizer import get_visualizer

    _viz = get_visualizer()
except Exception:  # keep the cli importable without the gui extras
    _viz = None

from .config import PROVIDERS, ensure_config, run_wizard  # noqa: E402
from .engines import ENGINES, fuzzy_match_engine  # noqa: E402
from .redact import redact  # noqa: E402

try:
    from pathlib import Path

    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    _session: Any = PromptSession(history=FileHistory(str(Path.home() / ".unsql_history")))
except Exception:  # pragma: no cover
    _session = None

BANNER = r"""
  _   _ _   _ ____   ___  _
 | | | | \ | / ___| / _ \| |     a live natural-language layer over your database
 | | | |  \| \___ \| | | | |     every input ends with ;   ·   help;   ·   exit;
 | |_| | |\  |___) | |_| | |___
  \___/|_| \_|____/ \__\_\_____|
"""

HELP = """
  Every input — command or plain English — must end with ';'.

  list;                     scan this machine for engines, drivers and instances
  connect <engine>;         connect (postgres, mysql, mariadb, mssql, oracle,
                            sqlite, db2, snowflake, aurora, access)
  disconnect;               close the current connection
  schema;  /  tables;       live structure of the connected database
  verify;                   live row counts and what changed this session
  changes;                  full session change log
  set apikey;               AI provider / key / model wizard
  set model <name>;         switch model
  set readonly on|off;      block every write statement
  set role viewer|analyst|admin;   viewer = SELECT only, analyst = no DDL
  gui;  /  gui terminal;    results dashboard (web / TUI)
  export csv|json|txt [path];      export the LAST SELECT result only
  help;   exit;

  Anything else is natural language, executed live against the connected
  database. Confirmation prompts for DROP TABLE, DELETE without WHERE and
  UPDATE without WHERE are always on.
"""

_SQL_STARTERS = (
    "select", "insert", "update", "delete", "create", "drop", "alter", "truncate",
    "with", "explain", "describe", "desc", "grant", "revoke", "begin", "commit",
    "rollback", "pragma", "show", "merge", "call", "use",
)

_WRITE_KINDS = {"insert", "update", "delete", "drop", "create", "alter", "truncate", "merge", "grant", "revoke"}
_DDL_KINDS = {"drop", "create", "alter", "truncate", "grant", "revoke"}

_PROBE_PORTS = {
    "postgresql": 5432, "mysql": 3306, "mariadb": 3307, "mssql": 1433,
    "oracle": 1521, "db2": 50000, "aurora": 3306,
}


class Repl:
    def __init__(self) -> None:
        self.cfg: dict = {}
        self.ai: AIClient | None = None
        self.engine: Any = None
        self.agent: Agent | None = None
        self.readonly = False
        self.role = "admin"
        self.last_result = None
        self.last_sql = ""
        self.changes: list[dict] = []

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _ask(self, prompt: str) -> str:
        if _session is not None:
            return _session.prompt(prompt)
        return input(prompt)

    def _prompt_label(self) -> str:
        if self.engine is not None:
            return f"UNSQL[{self.engine.engine_type}]> "
        return "CONNECT> "

    def _reload_ai(self) -> None:
        self.ai = AIClient(self.cfg)
        if self.engine is not None:
            self.agent = Agent(self.ai, self.engine, render.info)

    def _confirm(self, question: str) -> bool:
        try:
            return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return False

    # ── discovery / connection ───────────────────────────────────────────────

    def cmd_list(self) -> None:
        rows = []
        for name in sorted({"postgresql", "mysql", "mariadb", "mssql", "oracle", "sqlite",
                            "db2", "snowflake", "aurora", "access"}):
            driver = "installed" if name in ENGINES else "driver missing"
            port = _PROBE_PORTS.get(name)
            if name in ("sqlite", "access"):
                local = "file-based"
            elif port and _port_open("127.0.0.1", port):
                local = f"instance on :{port}"
            elif name == "snowflake":
                local = "cloud"
            else:
                local = "-"
            rows.append([name, driver, local])
        render.table(["engine", "driver", "local scan"], rows)

    def cmd_connect(self, token: str) -> None:
        if not token:
            render.warn("  usage: connect <engine>;")
            return
        cls = fuzzy_match_engine(token)
        if cls is None:
            render.error(f"  No engine (or no installed driver) for '{token}'.")
            render.info("  Run `list;` to see what is available.")
            return
        engine = cls()
        kwargs: dict[str, Any] = {}
        username = password = ""
        etype = engine.engine_type
        try:
            if etype in ("sqlite", "access"):
                default = "unsql.db" if etype == "sqlite" else ""
                path = input(f"  File path{f' [{default}]' if default else ''}: ").strip() or default
                if not path:
                    render.warn("  A file path is required.")
                    return
                kwargs["file_path"] = path
            elif etype == "snowflake":
                kwargs["account"] = input("  Account identifier: ").strip()
                username = input("  User: ").strip()
                password = getpass.getpass("  Password: ")
                kwargs["warehouse"] = input("  Warehouse (optional): ").strip()
                kwargs["database"] = input("  Database (optional): ").strip()
                kwargs["schema"] = input("  Schema [PUBLIC]: ").strip() or "PUBLIC"
            elif etype == "oracle":
                kwargs["host"] = input("  Host [localhost]: ").strip() or "localhost"
                kwargs["port"] = input("  Port [1521]: ").strip() or "1521"
                kwargs["service"] = input("  Service name [FREEPDB1]: ").strip() or "FREEPDB1"
                username = input("  User: ").strip()
                password = getpass.getpass("  Password: ")
            else:
                defaults = {"postgresql": "5432", "mysql": "3306", "mariadb": "3306",
                            "mssql": "1433", "db2": "50000", "aurora": "3306"}
                kwargs["host"] = input("  Host [localhost]: ").strip() or "localhost"
                kwargs["port"] = input(f"  Port [{defaults.get(etype, '')}]: ").strip() or defaults.get(etype, "")
                kwargs["database"] = input("  Database: ").strip()
                username = input("  User: ").strip()
                password = getpass.getpass("  Password: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return

        try:
            engine.connect(username, password, **kwargs)
        except Exception as exc:
            render.error(f"  {redact(str(exc))}")
            return

        self.engine = engine
        self.agent = Agent(self.ai, engine, render.info)
        render.info(f"  Connected to {engine.engine_name}.")

    def cmd_disconnect(self) -> None:
        if self.engine is None:
            render.warn("  Not connected.")
            return
        try:
            self.engine.disconnect()
        except Exception:
            pass
        self.engine = None
        self.agent = None
        render.info("  Disconnected.")

    # ── live introspection ───────────────────────────────────────────────────

    def cmd_schema(self) -> None:
        if not self._need_connection():
            return
        try:
            text = self.engine.schema_to_text("")
        except Exception as exc:
            render.error(f"  {redact(str(exc))}")
            return
        render.plain(text or "  (no tables yet)")

    def cmd_verify(self) -> None:
        if not self._need_connection():
            return
        try:
            tables = [t.name for t in (self.engine.get_schema() or [])]
        except Exception as exc:
            render.error(f"  {redact(str(exc))}")
            return
        if not tables:
            render.info("  No tables in the live database.")
            return
        rows = []
        touched = {c["table"] for c in self.changes if c.get("table")}
        for name in tables:
            try:
                res = self.engine.execute(f"SELECT COUNT(*) FROM {name}")
                count = res.rows[0][0] if res and res.rows else 0
            except Exception as exc:
                count = f"error: {redact(str(exc))[:40]}"
            rows.append([name, count, "changed" if name.lower() in touched else ""])
        render.table(["table", "live rows", "this session"], rows)

    def cmd_changes(self) -> None:
        if not self.changes:
            render.info("  No changes recorded this session.")
            return
        rows = [[str(i + 1), c["kind"].upper(), c.get("table") or "-", str(c["rowcount"]),
                 c["sql"].splitlines()[0][:70]] for i, c in enumerate(self.changes)]
        render.table(["#", "op", "table", "rows", "statement"], rows)

    def _need_connection(self) -> bool:
        if self.engine is None:
            render.warn("  Not connected. Use `connect <engine>;` first.")
            return False
        return True

    # ── safety guards ────────────────────────────────────────────────────────

    def _guard(self, stmt: str) -> bool:
        """Role / readonly checks + always-on confirmations. False = don't run."""
        kind = statement_kind(stmt)
        body = " ".join(
            l for l in stmt.splitlines() if not l.strip().startswith("--")
        ).lower()
        if kind in _WRITE_KINDS:
            if self.readonly:
                render.error("  readonly is on — write blocked.")
                return False
            if self.role == "viewer":
                render.error("  role viewer — only SELECT is allowed.")
                return False
            if self.role == "analyst" and kind in _DDL_KINDS:
                render.error("  role analyst — DDL is not allowed.")
                return False
        if kind == "drop" and " table " in f" {body} ":
            render.sql(stmt)
            return self._confirm("DROP TABLE — confirm?")
        if kind == "delete" and " where " not in body:
            render.sql(stmt)
            return self._confirm("DELETE without WHERE — confirm?")
        if kind == "update" and " where " not in body:
            render.sql(stmt)
            return self._confirm("UPDATE without WHERE — confirm?")
        return True

    # ── execution ────────────────────────────────────────────────────────────

    def _savepoint(self, name: str) -> bool:
        try:
            self.engine.execute(f"SAVEPOINT {name}")
            return True
        except Exception:
            return False

    def _rollback_to(self, name: str) -> None:
        try:
            self.engine.execute(f"ROLLBACK TO SAVEPOINT {name}")
        except Exception:
            try:
                self.engine.execute(f"ROLLBACK TO {name}")
            except Exception:
                pass

    def _record(self, stmt: str, result: Any) -> None:
        kind = statement_kind(stmt)
        if kind in _WRITE_KINDS:
            self.changes.append({
                "kind": kind,
                "table": _table_of(stmt),
                "rowcount": getattr(result, "rowcount", 0) or 0,
                "sql": stmt,
            })

    def _execute_one(self, stmt: str) -> bool:
        """Run one statement. Self-corrects once via the agent, rolls back that step only."""
        if not self._guard(stmt):
            render.info("  Step skipped.")
            return True
        sp = f"unsql_sp_{len(self.changes)}"
        has_sp = self._savepoint(sp)
        try:
            result = self.engine.execute(stmt)
        except Exception as exc:
            if has_sp:
                self._rollback_to(sp)
            error = redact(str(exc))
            if self.agent is None:
                render.error(f"  {error}")
                return False
            render.info("  agent · self-correcting this step")
            try:
                fixes = self.agent.repair(stmt, error)
            except AIError as ai_exc:
                render.error(f"  {redact(str(ai_exc))}")
                return False
            if not fixes:
                render.warn(f"  Step skipped after error: {error}")
                return True
            for fixed in fixes:
                if not self._guard(fixed):
                    continue
                try:
                    result = self.engine.execute(fixed)
                except Exception as exc2:
                    if has_sp:
                        self._rollback_to(sp)
                    render.error(f"  {redact(str(exc2))}")
                    return False
                self._show(result, fixed)
                self._record(fixed, result)
            return True
        self._show(result, stmt)
        self._record(stmt, result)
        return True

    def _show(self, result: Any, stmt: str) -> None:
        if result is None:
            return
        if result.is_select:
            header = _label(stmt)
            if header:
                render.info(f"  {header}")
            render.table(result.columns, result.rows)
            self.last_result = result
            if _viz is not None and result.rows is not None:
                try:
                    _viz.push_result(columns=list(result.columns), rows=list(result.rows),
                                     sql=stmt, title=f"{self.engine.engine_type} result")
                except Exception:
                    pass
        else:
            render.info(f"  OK ({result.rowcount} row(s) affected)")

    def _run_script(self, statements: list[str]) -> None:
        total = len(statements)
        for i, stmt in enumerate(statements, 1):
            if total > 1:
                render.plain(f"\n  [{i}/{total}]")
            render.sql(stmt)
            self.last_sql = stmt
            if not self._execute_one(stmt):
                render.error("  Run halted at this step; earlier steps are committed.")
                break
        try:
            self.engine.commit()
        except Exception:
            pass

    def cmd_sql(self, sql: str) -> None:
        if not self._need_connection():
            return
        self._run_script(split_statements(sql))

    def cmd_nl(self, text: str) -> None:
        if not self._need_connection():
            return
        assert self.agent is not None
        try:
            statements = self.agent.plan_and_write(text)
        except AIError as exc:
            render.error(f"  {redact(str(exc))}")
            return
        if not statements:
            render.warn("  The model produced no SQL.")
            return
        render.info(f"  agent · executing {len(statements)} statement(s) live")
        self._run_script(statements)

    # ── settings ─────────────────────────────────────────────────────────────

    def cmd_set(self, rest: str) -> None:
        head, _, val = rest.partition(" ")
        head, val = head.lower(), val.strip().lower()
        if head == "model":
            if not val:
                render.warn("  usage: set model <name>;")
                return
            from .config import set_model

            self.cfg = set_model(val)
            self._reload_ai()
            render.info(f"  Model set to {val}")
        elif head in ("apikey", "api_key", "provider", ""):
            self.cfg = run_wizard(self.cfg)
            self._reload_ai()
        elif head == "readonly":
            self.readonly = val in ("on", "true", "yes", "1")
            render.info(f"  readonly {'on' if self.readonly else 'off'}")
        elif head == "role":
            if val not in ("viewer", "analyst", "admin"):
                render.warn("  usage: set role viewer|analyst|admin;")
                return
            self.role = val
            render.info(f"  role {val}")
        else:
            render.warn("  usage: set apikey; | set model <name>; | set readonly on|off; | set role <r>;")

    # ── gui / export ─────────────────────────────────────────────────────────

    def cmd_gui(self, rest: str) -> None:
        if _viz is None:
            render.error("  GUI not available (install rich).")
            return
        if rest.strip().lower() in ("terminal", "tui"):
            _viz.launch_terminal()
            return
        has = self.last_result is not None and self.last_result.is_select
        try:
            url = _viz.launch_web(
                columns=list(self.last_result.columns) if has else None,
                rows=list(self.last_result.rows or []) if has else None,
                sql=self.last_sql,
            )
            render.info(f"  GUI dashboard at {url}")
        except Exception as exc:
            render.error(f"  Failed to launch GUI: {exc}")

    def cmd_export(self, rest: str) -> None:
        parts = rest.split(None, 1)
        fmt = parts[0].lower() if parts else ""
        path = parts[1].strip() if len(parts) > 1 else None
        if fmt not in ("csv", "json", "txt"):
            render.warn("  usage: export csv|json|txt [path];  (exports the last SELECT only)")
            return
        res = self.last_result
        if not res or not res.is_select or not res.rows:
            render.warn("  Nothing to export — run a query that returns rows first.")
            return
        out = path or f"unsql_export.{fmt}"
        try:
            if fmt == "txt":
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(" | ".join(str(c) for c in res.columns) + "\n")
                    for row in res.rows:
                        fh.write(" | ".join("NULL" if v is None else str(v) for v in row) + "\n")
                target: Any = out
            elif _viz is None:
                render.error("  CSV/JSON export needs the gui extras.")
                return
            elif fmt == "csv":
                target = _viz.export_csv(out, columns=list(res.columns), rows=list(res.rows))
            else:
                target = _viz.export_json(out, columns=list(res.columns), rows=list(res.rows))
            render.info(f"  Exported {len(res.rows)} rows of the last result to {target}")
        except Exception as exc:
            render.error(f"  Export failed: {exc}")

    # ── loop ─────────────────────────────────────────────────────────────────

    def dispatch(self, raw: str) -> bool:
        line = raw.strip()
        if not line:
            return True
        if not line.endswith(";"):
            render.warn("  Every input must end with ';' — commands and plain English alike.")
            return True
        text = line[:-1].strip()
        if not text:
            return True
        head, _, rest = text.partition(" ")
        head_l, rest = head.lower(), rest.strip()

        if head_l in ("exit", "quit"):
            return False
        if head_l == "help":
            render.plain(HELP)
        elif head_l == "list":
            self.cmd_list()
        elif head_l == "connect":
            self.cmd_connect(rest)
        elif head_l == "disconnect":
            self.cmd_disconnect()
        elif head_l in ("schema", "tables"):
            self.cmd_schema()
        elif head_l == "verify":
            self.cmd_verify()
        elif head_l == "changes":
            self.cmd_changes()
        elif head_l == "set":
            self.cmd_set(rest)
        elif head_l == "gui":
            self.cmd_gui(rest)
        elif head_l == "export":
            self.cmd_export(rest)
        elif head_l in _SQL_STARTERS:
            self.cmd_sql(text)
        else:
            self.cmd_nl(text)
        return True

    def run(self) -> int:
        render.plain(BANNER)
        self.cfg = ensure_config()
        self._reload_ai()
        spec = PROVIDERS.get(self.cfg.get("provider", ""), {})
        render.info(f"  {spec.get('name', self.cfg.get('provider'))} · {self.cfg.get('model')}\n")
        while True:
            try:
                line = self._ask(self._prompt_label())
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            try:
                if not self.dispatch(line):
                    break
            except Exception as exc:  # never die on a single bad input
                render.error(f"  {redact(str(exc))}")
        if self.engine is not None:
            try:
                self.engine.disconnect()
            except Exception:
                pass
        render.plain("  bye")
        return 0


def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _label(stmt: str) -> str:
    for line in stmt.splitlines():
        s = line.strip()
        if s.startswith("--"):
            return s.lstrip("-").strip()
        if s:
            break
    return ""


_TABLE_WORDS = ("into", "from", "update", "table")


def _table_of(stmt: str) -> str | None:
    words = [w.strip("`\"[];,()") for w in stmt.replace("\n", " ").split()]
    for i, w in enumerate(words[:-1]):
        if w.lower() in _TABLE_WORDS:
            cand = words[i + 1]
            if cand.lower() in ("if", "exists"):
                continue
            return cand.split(".")[-1].lower()
    return None


def main(argv: list[str] | None = None) -> int:
    from .unsql import main as launcher_main

    return launcher_main(argv if argv is not None else sys.argv[1:])
