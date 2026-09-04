"""terminal capability setup — idempotent, never raises."""
import sys

_DONE = False


def setup_console() -> None:
    global _DONE
    if _DONE:
        return
    if sys.platform == "win32":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k32.SetConsoleOutputCP(65001)  # UTF-8 output
            k32.SetConsoleCP(65001)  # UTF-8 input
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    _DONE = True


def safe_glyph(console, preferred: str, fallback: str) -> str:
    enc = getattr(console, "encoding", None) or "utf-8"
    try:
        preferred.encode(enc)
        return preferred
    except (UnicodeEncodeError, LookupError):
        return fallback
