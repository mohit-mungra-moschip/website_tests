"""
logger.py — central logging for pytest-fixer.

Every node imports from here. Logs go to:
  1. Terminal   — rich-formatted, colour-coded by level
  2. File       — plain text, one line per event, saved to ./logs/pytest-fixer.log

Usage in any node:
    from logger import log
    log.info("Reading file", file="src/calc.py", size="1.2 KB")
    log.success("Fix applied", file="src/calc.py")
    log.llm("Calling Groq", model="llama-3.3-70b-versatile", task="fix_code")
    log.warn("Skipping large file", file="src/generated.py", size="120 KB")
    log.error("Write failed", file="src/calc.py", reason="permission denied")
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

# ── Setup ─────────────────────────────────────────────────────────────────────

LOG_DIR  = Path("logs")
_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILE = LOG_DIR / f"pytest-fixer_{_timestamp}.log"

_console = Console(highlight=False)


def _setup_file_logger() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("pytest_fixer")
    logger.setLevel(logging.DEBUG)

    fh = None
    if not logger.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)

    # Add the file handler to the root logger to capture all system-wide, AIWrapper, and LangChain logs
    root_logger = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root_logger.handlers):
        if not fh:
            fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        # Reuse or create formatting for root logger
        root_formatter = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        if fh.formatter is None or fh.formatter._fmt != root_formatter._fmt:
            # Create a separate handler for root logger to use name format
            root_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
            root_fh.setFormatter(root_formatter)
            root_logger.addHandler(root_fh)
        else:
            root_logger.addHandler(fh)

    return logger


_file_logger = _setup_file_logger()

# ── Level colours and icons ───────────────────────────────────────────────────

_LEVELS = {
    "step":    ("bold blue",    ">"),
    "info":    ("dim white",    "-"),
    "success": ("bold green",   "*"),
    "llm":     ("bold magenta", "~"),
    "warn":    ("yellow",       "!"),
    "error":   ("bold red",     "x"),
}


def _fmt_kv(kwargs: dict[str, Any]) -> str:
    """Format key=value pairs compactly: file=src/calc.py  size=1.2KB"""
    return "  ".join(f"[dim]{k}=[/dim]{v}" for k, v in kwargs.items())


# ── Logger class ──────────────────────────────────────────────────────────────

class _Logger:
    def _emit(self, level: str, msg: str, **kwargs: Any) -> None:
        colour, icon = _LEVELS[level]
        ts = datetime.now().strftime("%H:%M:%S")

        # Terminal line
        line = Text.assemble(
            (f"{ts} ", "dim"),
            (f"{icon} ", colour),
            (msg, colour if level in ("step", "success", "error") else "white"),
        )
        if kwargs:
            line.append("  " + "  ".join(f"{k}={v}" for k, v in kwargs.items()), style="dim")
        _console.print(line)

        # File line (plain)
        kv_str = "  ".join(f"{k}={v}" for k, v in kwargs.items())
        _file_logger.log(
            logging.WARNING if level == "warn" else
            logging.ERROR   if level == "error" else
            logging.INFO,
            f"[{level.upper():<7}] {msg}  {kv_str}".rstrip()
        )

    def step(self, msg: str, **kw):    self._emit("step",    msg, **kw)
    def info(self, msg: str, **kw):    self._emit("info",    msg, **kw)
    def success(self, msg: str, **kw): self._emit("success", msg, **kw)
    def llm(self, msg: str, **kw):     self._emit("llm",     msg, **kw)
    def warn(self, msg: str, **kw):    self._emit("warn",    msg, **kw)
    def warning(self, msg: str, **kw): self._emit("warn",    msg, **kw)
    def error(self, msg: str, **kw):   self._emit("error",   msg, **kw)

    def separator(self, title: str = "") -> None:
        _console.rule(f"[dim]{title}[/dim]" if title else "")
        _file_logger.info(f"{'─'*40} {title}")

    def node_start(self, name: str) -> float:
        """Call at the top of every node. Returns start time."""
        self.separator(name)
        _file_logger.info(f"NODE START: {name}")
        return time.monotonic()

    def node_end(self, name: str, start: float) -> None:
        elapsed = time.monotonic() - start
        self.info(f"Node done", node=name, elapsed=f"{elapsed:.2f}s")
        _file_logger.info(f"NODE END:   {name}  elapsed={elapsed:.2f}s")

    @property
    def log_path(self) -> Path:
        return LOG_FILE.resolve()


log = _Logger()


def get_logger(name: str = "") -> _Logger:
    """Return the singleton logger. 'name' is ignored (kept for API compatibility)."""
    return log


# Standard Python logger used by AIWrapper / LangChain internals.
# Any module that previously did `from common_utils.LLMConfig import logger`
# should now do `from common_utils.logger import logger`.
import logging as _logging
_logging.basicConfig(level=_logging.INFO)
logger = _logging.getLogger("AIWrapper")

import sys
import re

class Tee:
    def __init__(self, original_stream, log_file_path):
        self.original_stream = original_stream
        self.log_file_path = log_file_path

    def write(self, data):
        self.original_stream.write(data)
        if data:
            try:
                # Strip ANSI escape sequences so log file has clean plain text
                clean_data = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', data)
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(clean_data)
            except Exception:
                pass

    def flush(self):
        self.original_stream.flush()

# Redirect stdout and stderr so that all print statements and rich console prints go to the log file
sys.stdout = Tee(sys.stdout, LOG_FILE)
sys.stderr = Tee(sys.stderr, LOG_FILE)

