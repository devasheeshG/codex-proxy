# Path: app/logger.py
# Description: JSON structured logging with per-request correlation fields (request_id, endpoint, method).

import json
import logging
import logging.config
import re

from app.config import get_settings


class RequestContextFilter(logging.Filter):
    """Attach the current request's correlation fields to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        from app.utils import request_context

        context = request_context.get_request_context()
        record.request_id = context.get("request_id", "-")
        record.endpoint = context.get("endpoint", "-")
        record.method = context.get("method", "-")

        # uvicorn.access records bypass the middleware, so recover method/endpoint from the message itself.
        if record.name == "uvicorn.access":
            match = re.search(r'"(\w+)\s+([^\s]+)\s+HTTP', record.getMessage())
            if match:
                record.method = match.group(1)
                record.endpoint = match.group(2)

        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "request_id": record.request_id,
            "endpoint": record.endpoint,
            "method": record.method,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def configure_logging() -> None:
    """Install the JSON logging configuration. Call once at process startup (app lifespan or script entrypoint)."""
    level = get_settings().LOG_LEVEL.upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_context": {"()": RequestContextFilter}},
            "formatters": {"json": {"()": JsonFormatter, "datefmt": "%Y-%m-%dT%H:%M:%S"}},
            "handlers": {
                "json_stdout": {
                    "formatter": "json",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "filters": ["request_context"],
                }
            },
            "loggers": {
                "": {"handlers": ["json_stdout"], "level": level},
                "uvicorn.error": {"handlers": ["json_stdout"], "level": level, "propagate": False},
                "uvicorn.access": {"handlers": ["json_stdout"], "level": level, "propagate": False},
                "httpx": {"handlers": ["json_stdout"], "level": "WARNING", "propagate": False},
                "httpcore": {"handlers": ["json_stdout"], "level": "WARNING", "propagate": False},
                "sqlalchemy.engine": {"handlers": ["json_stdout"], "level": "WARNING", "propagate": False},
            },
        }
    )


def get_logger() -> logging.Logger:
    """Return the shared application logger. Logging is installed once via configure_logging() at startup."""
    return logging.getLogger("app")
