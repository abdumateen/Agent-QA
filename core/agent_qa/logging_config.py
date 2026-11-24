"""JSON logging shared by the core service and MCP process."""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

import structlog
from structlog.types import Processor

from agent_qa.config import Settings, get_settings

_CONFIGURATION_LOCK = threading.RLock()
_HANDLER_NAMES = frozenset({"agent_qa.stderr", "agent_qa.file"})


def configure_logging(settings: Settings | None = None) -> None:
    """Configure process-wide JSON logging without writing to stdout."""
    config = settings if settings is not None else get_settings()

    with _CONFIGURATION_LOCK:
        config.log_dir.mkdir(parents=True, exist_ok=True)

        shared_processors: list[Processor] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            structlog.processors.UnicodeDecoder(),
        ]

        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                ),
            ],
        )

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.set_name("agent_qa.stderr")
        stderr_handler.setLevel(config.log_level)
        stderr_handler.setFormatter(formatter)

        try:
            file_handler = RotatingFileHandler(
                filename=config.log_path,
                maxBytes=config.log_max_bytes,
                backupCount=config.log_backup_count,
                encoding="utf-8",
                errors="backslashreplace",
            )
        except OSError:
            stderr_handler.close()
            raise

        file_handler.set_name("agent_qa.file")
        file_handler.setLevel(config.log_level)
        file_handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        _detach_handlers(root_logger)
        root_logger.setLevel(config.log_level)
        root_logger.addHandler(stderr_handler)
        root_logger.addHandler(file_handler)

        for logger_name in (
            "agent_qa",
            "mcp",
            "uvicorn",
            "uvicorn.error",
            "uvicorn.access",
            "fastapi",
        ):
            logger = logging.getLogger(logger_name)
            _detach_handlers(logger)
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
            logger.disabled = False

        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                *shared_processors,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=False,
        )

        logging.captureWarnings(True)


def get_logger(name: str = "agent_qa") -> structlog.stdlib.BoundLogger:
    """Return a structured logger using the current logging configuration."""
    return structlog.stdlib.get_logger(name)


def _detach_handlers(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        if handler.get_name() in _HANDLER_NAMES:
            handler.close()