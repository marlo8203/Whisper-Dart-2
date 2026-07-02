"""Central logging configuration for Whisper Dart.

Two rotating log files live in ``ui/logs/``:

* ``whisperdart.log`` — the full application log (INFO and above). The
  running narrative: startup, uploads, transcription jobs, saves.
* ``whisperdart.err`` — warnings and errors only. This is the file to open
  first when something goes wrong; it stays small and noise-free.

The console (stdout) mirrors INFO+ so the terminal stays useful while the
app runs. Uncaught exceptions — on the main thread and in worker threads —
are routed here with full tracebacks instead of vanishing to stderr.

Call :func:`setup_logging` once at startup, then ``get_logger(__name__)``
in each module.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from ui.constants import ERR_FILE, LOG_DIR, LOG_FILE

# Rotate at 5 MB, keep 5 old files — bounded disk use, plenty of history.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5

_FMT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logging. Idempotent — safe to call again under the
    gradio hot-reloader without stacking duplicate handlers."""
    global _configured

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()

    if _configured:
        return root

    root.setLevel(logging.DEBUG)  # handlers do the actual filtering
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    # Full application log — INFO and above.
    log_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8",
    )
    log_handler.setLevel(level)
    log_handler.setFormatter(formatter)

    # Errors-only log — WARNING and above.
    err_handler = RotatingFileHandler(
        ERR_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8",
    )
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(formatter)

    # Console mirror so the terminal stays informative.
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)

    for h in (log_handler, err_handler, console):
        root.addHandler(h)

    # Quiet chatty third-party libraries so our own logs stay readable.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "multipart",
                  "gradio", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _install_excepthooks(root)

    _configured = True
    root.info("Logging initialised → %s (all) · %s (errors)", LOG_FILE, ERR_FILE)
    return root


def _install_excepthooks(root: logging.Logger) -> None:
    """Route uncaught exceptions (main + worker threads) into the logs with a
    full traceback, so nothing fails silently."""

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        root.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook

    def _thread_hook(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        root.critical(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Assumes :func:`setup_logging` has run."""
    return logging.getLogger(name)
