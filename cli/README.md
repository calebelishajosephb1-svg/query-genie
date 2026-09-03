# UNSQL

An AI layer that sits directly over your database. Type plain English, get
dialect-correct SQL, review it, run it — all in one terminal session.

Supported engines: PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, SQLite,
Db2, Snowflake, Aurora, Access.

## Install

```bash
cd cli
python -m pip install -e .            # core (SQLite works out of the box)
python -m pip install -e ".[all]"     # + the common drivers
python -m pip install -e ".[oracle]"  # or just the one you need
```

## Run

```bash
unsql            # first run walks you through provider / key / model
unsql --setup    # re-run the wizard
```

Providers: OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA NIM, Ollama,
LM Studio, or any OpenAI-compatible endpoint. Config lives in
`~/.unsql_config` (0600). Environment overrides: `UNSQL_PROVIDER`,
`UNSQL_API_KEY`, `UNSQL_MODEL`, `UNSQL_BASE_URL`.

## Session

```
unsql> connect sqlite
  File path [unsql.db]: demo.db
unsql[sqlite]> build me a restaurant chain schema with realistic seed data
  ... SQL is printed, you confirm, it runs ...
unsql[sqlite]> which branch had the highest margin last quarter?
unsql[sqlite]> save report.sql
```

## Commands

| command | what it does |
| --- | --- |
| `connect <engine>` | connect (prompts for the params that engine needs) |
| `disconnect` | close the connection |
| `engines` | which engines have their driver installed |
| `tables` / `schema` | introspect the live database |
| `script <engine> <request>` | offline: plan pass + full script, no DB needed |
| `save <file>` | write the last SQL to a file |
| `set` / `set model <name>` | re-run the wizard / switch model |
| `auto on\|off` | run generated SQL without confirming |
| `help` / `exit` | |

Anything else is natural language — unless it starts with a SQL keyword, in
which case it runs verbatim.

## Layout

```
unsql/
  repl.py      the REPL
  ai.py        streaming client (openai / anthropic / gemini wire formats)
  config.py    provider registry + config store
  prompts.py   per-dialect rules and prompt builders
  render.py    tables, SQL highlighting, status lines
  redact.py    keeps secrets out of printed errors
  engines/     one adapter per RDBMS
```
