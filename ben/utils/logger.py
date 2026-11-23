"""Logging configuration for BEN using Rich."""

import logging
import sys
from rich.console import Console
from rich.logging import RichHandler

console = Console()

def setup_logger(name: str = "ben", level: int = logging.INFO) -> logging.Logger:
    """Configure and return a standardized logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if setup_logger is called multiple times
    if not logger.handlers:
        handler = RichHandler(
            console=console,
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
            markup=True
        )
        formatter = logging.Formatter("%(message)s", datefmt="[%X]")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False

    return logger

logger = setup_logger()
