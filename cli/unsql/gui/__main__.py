"""`python -m unsql.gui [--port X] [--host H] [--no-browser] [--terminal]`"""
from __future__ import annotations

import argparse
import time

from .visualizer import GUIVisualizer


def main() -> int:
    ap = argparse.ArgumentParser(description="UNSQL GUI dashboard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--terminal", action="store_true", help="demo 250-row table")
    args = ap.parse_args()

    viz = GUIVisualizer()
    viz.config.web_port = args.port
    viz.config.web_host = args.host
    viz.config.auto_open_browser = not args.no_browser

    if args.terminal:
        viz.launch_terminal(
            columns=["id", "name", "value"],
            rows=[(i, f"row_{i}", i * 10) for i in range(1, 251)],
            title="Demo — 250 rows (paginate with n/p)",
        )
        return 0

    demo_cols = ["id", "name", "revenue"]
    demo_rows = [(i, f"branch_{i % 5}", 1000 + i * 13) for i in range(1, 501)]
    url = viz.launch_web(
        columns=demo_cols,
        rows=demo_rows,
        sql="-- demo: SELECT * FROM demo",
        title="UNSQL Demo Dashboard",
    )
    print(f"GUI running at {url}\nPress Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nGUI stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
