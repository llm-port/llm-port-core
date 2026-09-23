import logging
import sys
from typing import Any

from loguru import logger
from opentelemetry.trace import INVALID_SPAN, INVALID_SPAN_CONTEXT, get_current_span

from llm_port_backend.settings import settings


class InterceptHandler(logging.Handler):
    """
    Default handler from examples in loguru documentation.

    This handler intercepts all log requests and
    passes them to loguru.

    For more info see:
    https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        """
        Propagates logs to loguru.

        :param record: record to log.
        """
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level,
            record.getMessage(),
        )


def record_formatter(record: dict[str, Any]) -> str:  # pragma: no cover
    """
    Formats the record.

    This function formats message
    by adding extra trace information to the record.

    :param record: record information.
    :return: format string.
    """
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
        "| <level>{level: <8}</level> "
        "| <magenta>trace_id={extra[trace_id]}</magenta> "
        "| <blue>span_id={extra[span_id]}</blue> "
        "| <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
        "- <level>{message}</level>\n"
    )

    span = get_current_span()
    record["extra"]["span_id"] = 0
    record["extra"]["trace_id"] = 0
    if span != INVALID_SPAN:
        span_context = span.get_span_context()
        if span_context != INVALID_SPAN_CONTEXT:
            record["extra"]["span_id"] = format(span_context.span_id, "016x")
            record["extra"]["trace_id"] = format(span_context.trace_id, "032x")

    if record["exception"]:
        log_format = f"{log_format}{{exception}}"

    return log_format


def configure_logging() -> None:  # pragma: no cover
    """Configures logging."""
    intercept_handler = InterceptHandler()

    logging.basicConfig(handlers=[intercept_handler], level=logging.NOTSET)

    for logger_name in logging.root.manager.loggerDict:
        if logger_name.startswith("uvicorn."):
            logging.getLogger(logger_name).handlers = []
        if logger_name.startswith("taskiq."):
            logging.getLogger(logger_name).root.handlers = [intercept_handler]

    # change handler for default uvicorn logger
    logging.getLogger("uvicorn").handlers = [intercept_handler]
    logging.getLogger("uvicorn.access").handlers = [intercept_handler]

    # set logs output, level and format
    logger.remove()
    _use_utf8_stdout()
    logger.add(
        sys.stdout,
        level=settings.log_level.value,
        format=record_formatter,  # type: ignore
    )


def _use_utf8_stdout() -> None:
    """Let stdout carry the characters this service actually logs.

    Windows gives a redirected stdout the cp1252 codec, and plenty of log
    lines here contain an arrow or a box rule. Each one raised
    UnicodeEncodeError inside loguru's sink, which swallowed it as
    "--- End of logging error ---" and dropped the line -- so the log was
    missing exactly the messages that described what was happening, and only
    when running headless, which is when the log is the only thing there is.

    ``errors="replace"`` because a degraded character beats a lost line.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:  # pragma: no cover - a wrapper without it
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # pragma: no cover - already detached
        pass
