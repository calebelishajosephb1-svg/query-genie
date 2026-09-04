"""
unsql/repl.py
-------------
The lean UNSQL REPL: an AI layer that sits directly on top of your database.

Type plain English -> UNSQL writes dialect-correct SQL against the live schema,
shows it, asks once, then runs it and prints the result table.
Type SQL -> it runs verbatim.
Type a `word` command (connect, schema, tables, set, save, help, exit) -> it acts.
"""
from __future__ import annotations

import getpass
import sys
from typing import Any

from .core.termsetup import setup_console

setup_console()

from . import render  # noqa: E402
from .ai import AIClient, AIError  # noqa: E402

try:
    from .gui.visualizer import get_visualizer

    _viz = get_visualizer()
except Exception:  # keep cli importable without gui
    _viz = None
from .config import PROVIDERS, ensure_config, load_config, run_wizard
from .engines import ENGINES, fuzzy_match_engine
from .prompts import live_system, plan_system, script_system, strip_fences
from .redact import redact

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from pathlib import Path

    _session: Any = PromptSession(history=FileHistory(str(Path.home() / ".unsql_history")))
except Exception:  # pragma: no cover
    _session = None

BANNER = r"""
  _   _ _   _ ____   ___  _
 | | | | \ | / ___| / _ \| |     natural language -> SQL, on your database
 | | | |  \| \___ \| | | | |     type `help` for commands, `exit` to quit
 | |_| | |\  |___) | |_| | |___
  \___/|_| \_|____/ \__\_\_____|
"""

HELP = """
  connect <engine>        connect to a database (postgres, mysql, mariadb, mssql,
                          oracle, sqlite, db2, snowflake, aurora, access)
  disconnect              close the current connection
  engines                 list available engines
  tables                  list tables in the connected database
  schema                  print the live schema UNSQL sends to the model
  script <engine> <text>  offline mode: plan + full script for an engine, no DB needed
  save <file>             write the last SQL (or last result) to a file
  set                     re-run the AI provider / key / model wizard
  set model <name>        switch model
  auto on|off             run generated SQL without asking (default off)
  gui [terminal]          open results dashboard (web by default, terminal for TUI)
  export csv|json [path]  save last SELECT to CSV/JSON (sanitized, inject-safe)
  help                    this text
  exit                    quit

  Anything else is treated as natural language (or raw SQL if it starts with a
  SQL keyword) and executed against the connected database.
"""

_SQL_STARTERS = (
    "select", "insert", "update", "delete", "create", "drop", "alter", "truncate",
    "with", "explain", "describe", "desc", "grant", "revoke", "begin", "commit",
    "rollback", "pragma", "show", "merge", "call", "use",
)


class Repl:
    def __init__(self) -> None:
        self.cfg: dict = {}
        self.ai: AIClient | None = None
        self.engine: Any = None
        self.auto = False
        self.last_sql = ""
        self.last_result = None

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _ask(self, prompt: str) -> str:
        if _session is not None:
            return _session.prompt(prompt)
        return input(prompt)

    def _prompt_label(self) -> str:
        if self.engine is not None:
            return f"unsql[{self.engine.engine_type}]> "
        return "unsql> "

    def _reload_ai(self) -> None:
        self.ai = AIClient(self.cfg)

    # ── connection ───────────────────────────────────────────────────────────

    def cmd_connect(self, token: str) -> None:
        if not token:
            render.warn("  usage: connect <engine>")
            return
        cls = fuzzy_match_engine(token)
        if cls is None:
            render.error(f"  No engine (or no installed driver) for '{token}'.")
            render.info("  Available: " + ", ".join(sorted(ENGINES)))
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
        render.info("  Disconnected.")

    # ── introspection ────────────────────────────────────────────────────────

    def _schema_text(self, nl: str = "") -> str:
        if self.engine is None:
            return ""
        try:
            return self.engine.schema_to_text(nl)
        except Exception as exc:
            render.warn(f"  Could not read schema: {redact(str(exc))}")
            return ""

    def cmd_schema(self) -> None:
        if self.engine is None:
            render.warn("  Connect to a database first.")
            return
        text = self._schema_text()
        render.plain(text or "  (no tables yet)")

    def cmd_tables(self) -> None:
        if self.engine is None:
            render.warn("  Connect to a database first.")
            return
        try:
            names = [t.name for t in (self.engine.get_schema() or [])]
        except Exception as exc:
            render.error(f"  {redact(str(exc))}")
            return
        if not names:
            render.info("  No tables.")
            return
        render.table(["table"], [[n] for n in names])

    # ── execution ────────────────────────────────────────────────────────────

    def _run_sql(self, script: str) -> None:
        statements = _split_statements(script)
        for stmt in statements:
            try:
                result = self.engine.execute(stmt)
            except Exception as exc:
                render.error(f"  {redact(str(exc))}")
                try:
                    self.engine.rollback()
                except Exception:
                    pass
                return
            if result is None:
                continue
            if result.is_select:
                render.table(result.columns, result.rows)
                self.last_result = result
            else:
                render.info(f"  OK ({result.rowcount} row(s) affected)")
        try:
            self.engine.commit()
        except Exception:
            pass

    def cmd_sql(self, sql: str) -> None:
        if self.engine is None:
            render.warn("  Connect to a database first.")
            return
        self.last_sql = sql
        self._run_sql(sql)

    def cmd_nl(self, text: str) -> None:
        if self.engine is None:
            render.warn("  Connect to a database first, or use `script <engine> <request>`.")
            return
        assert self.ai is not None
        schema = self._schema_text(text)
        render.info("  thinking...")
        try:
            raw = self.ai.complete(live_system(self.engine.engine_type, schema), text)
        except AIError as exc:
            render.error(f"  {redact(str(exc))}")
            return
        sql = strip_fences(raw)
        if not sql:
            render.warn("  The model produced no SQL.")
            return
        self.last_sql = sql
        render.plain()
        render.sql(sql)
        if not self.auto:
            try:
                answer = input("  Run this? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if answer not in ("", "y", "yes"):
                render.info("  Skipped. `save <file>` still works.")
                return
        self._run_sql(sql)

    def cmd_script(self, rest: str) -> None:
        """Offline: plan pass + full script pass for any engine, no DB needed."""
        parts = rest.split(None, 1)
        if len(parts) < 2:
            render.warn("  usage: script <engine> <request>")
            return
        token, request = parts[0], parts[1]
        cls = fuzzy_match_engine(token)
        engine_type = cls().engine_type if cls else token.lower()
        assert self.ai is not None
        schema = self._schema_text(request) if self.engine is not None else ""

        render.info("  pass 1/2 — planning")
        try:
            plan = self.ai.complete(plan_system(engine_type), request, on_delta=_echo)
            render.plain("\n")
            render.info("  pass 2/2 — writing SQL")
            script = self.ai.complete(
                script_system(engine_type, schema=schema),
                f"PLAN:\n{plan}\n\nORIGINAL REQUEST:\n{request}",
                on_delta=_echo,
            )
        except AIError as exc:
            render.error(f"\n  {redact(str(exc))}")
            return
        render.plain("\n")
        self.last_sql = strip_fences(script)
        render.info("  Done. `save <file>` writes the script; paste it or run it yourself.")

    # ── misc commands ────────────────────────────────────────────────────────

    def cmd_save(self, path: str) -> None:
        if not path:
            render.warn("  usage: save <file>")
            return
        if not self.last_sql:
            render.warn("  Nothing to save yet.")
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.last_sql.rstrip() + "\n")
        except OSError as exc:
            render.error(f"  {exc}")
            return
        render.info(f"  Wrote {path}")

    def cmd_set(self, rest: str) -> None:
        if rest.startswith("model"):
            model = rest[5:].strip()
            if not model:
                render.warn("  usage: set model <name>")
                return
            from .config import set_model

            self.cfg = set_model(model)
            self._reload_ai()
            render.info(f"  Model set to {model}")
            return
        self.cfg = run_wizard(self.cfg)
        self._reload_ai()

    def cmd_engines(self) -> None:
        rows = [[name, "ready" if name in ENGINES else "driver missing"] for name in sorted(
            {"postgresql", "mysql", "mariadb", "mssql", "oracle", "sqlite", "db2",
             "snowflake", "aurora", "access"})]
        render.table(["engine", "status"], rows)

    # ── loop ─────────────────────────────────────────────────────────────────

    def dispatch(self, line: str) -> bool:
        """Returns False when the REPL should exit."""
        text = line.strip().rstrip(";").strip() if line.strip().endswith(";") else line.strip()
        if not text:
            return True
        head, _, rest = text.partition(" ")
        head_l = head.lower()
        rest = rest.strip()

        if head_l in ("exit", "quit", "\\q"):
            return False
        if head_l == "help":
            render.plain(HELP)
        elif head_l == "connect":
            self.cmd_connect(rest)
        elif head_l == "disconnect":
            self.cmd_disconnect()
        elif head_l == "engines":
            self.cmd_engines()
        elif head_l == "tables":
            self.cmd_tables()
        elif head_l == "schema":
            self.cmd_schema()
        elif head_l == "script":
            self.cmd_script(rest)
        elif head_l == "save":
            self.cmd_save(rest)
        elif head_l == "set":
            self.cmd_set(rest)
        elif head_l == "auto":
            self.auto = rest.lower() in ("on", "true", "yes", "1")
            render.info(f"  auto-run {'on' if self.auto else 'off'}")
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
            except Exception as exc:  # never die on a single bad command
                render.error(f"  {redact(str(exc))}")
        if self.engine is not None:
            try:
                self.engine.disconnect()
            except Exception:
                pass
        render.plain("  bye")
        return 0


def _echo(delta: str) -> None:
    sys.stdout.write(delta)
    sys.stdout.flush()


def _split_statements(script: str) -> list[str]:
    """Split on ';' outside quotes; keeps PL/SQL-ish blocks intact via '/' lines."""
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(script):
        ch = script[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "-" and script[i : i + 2] == "--":
            while i < len(script) and script[i] != "\n":
                i += 1
            buf.append("\n")
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail and tail != "/":
        out.append(tail)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        render.plain(HELP)
        return 0
    if argv and argv[0] == "--setup":
        run_wizard(load_config())
        return 0
    return Repl().run()
