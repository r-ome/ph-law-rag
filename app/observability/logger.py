from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from app.config import settings

_configured = False
_HANDLER_MARKER = "_raglab_observability_handler"


def _raglab_name(name: str | None) -> str | None:
	if name and name.startswith("app."):
		return "raglab." + name[4:]
	if name == "app":
		return "raglab"
	return name


def get_logger(name: str | None = None):
	return structlog.get_logger(_raglab_name(name))


def _owned_handler(handler: logging.Handler) -> bool:
	return bool(getattr(handler, _HANDLER_MARKER, False))


def _mark_owned(handler: logging.Handler) -> logging.Handler:
	setattr(handler, _HANDLER_MARKER, True)
	return handler


def _remove_owned_handlers(logger: logging.Logger) -> None:
	for handler in list(logger.handlers):
		if _owned_handler(handler):
			logger.removeHandler(handler)
			handler.close()


def configure_logging(*, force: bool = False) -> None:
	"""Configure structlog under the raglab.* logger namespace.

	The function is safe to call from multiple entry points. A forced reconfigure only
	removes handlers previously installed by this module.
	"""
	global _configured
	if _configured and not force:
		return

	level_name = "DEBUG" if settings.debug else settings.log_level.upper()
	level = getattr(logging, level_name, logging.INFO)

	shared_processors = [
		structlog.contextvars.merge_contextvars,
		structlog.processors.add_log_level,
		structlog.processors.TimeStamper(fmt="iso"),
		structlog.processors.StackInfoRenderer(),
		structlog.processors.format_exc_info,
	]

	structlog.configure(
		processors=[
			*shared_processors,
			structlog.stdlib.add_logger_name,
			structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
		],
		logger_factory=structlog.stdlib.LoggerFactory(),
		wrapper_class=structlog.stdlib.BoundLogger,
		cache_logger_on_first_use=True,
	)

	raglab_logger = logging.getLogger("raglab")
	_remove_owned_handlers(raglab_logger)
	raglab_logger.setLevel(logging.DEBUG)
	raglab_logger.propagate = False

	foreign_pre_chain = [
		structlog.contextvars.merge_contextvars,
		structlog.processors.add_log_level,
		structlog.processors.TimeStamper(fmt="iso"),
		structlog.stdlib.add_logger_name,
	]

	console_formatter = structlog.stdlib.ProcessorFormatter(
		processor=structlog.dev.ConsoleRenderer(colors=False),
		foreign_pre_chain=foreign_pre_chain,
	)
	console_handler = _mark_owned(logging.StreamHandler(sys.stderr))
	console_handler.setLevel(level)
	console_handler.setFormatter(console_formatter)
	raglab_logger.addHandler(console_handler)

	if settings.log_to_file:
		try:
			log_dir = Path(settings.log_dir)
			log_dir.mkdir(parents=True, exist_ok=True)
			file_formatter = structlog.stdlib.ProcessorFormatter(
				processor=structlog.processors.JSONRenderer(),
				foreign_pre_chain=foreign_pre_chain,
			)
			file_handler = _mark_owned(
				RotatingFileHandler(
					log_dir / "app.log",
					maxBytes=settings.log_max_bytes,
					backupCount=settings.log_backup_count,
					encoding="utf-8",
				)
			)
			file_handler.setLevel(logging.DEBUG)
			file_handler.setFormatter(file_formatter)
			raglab_logger.addHandler(file_handler)
		except OSError:
			raglab_logger.warning("log_file_unavailable", log_dir=str(settings.log_dir), exc_info=True)

	_configured = True

