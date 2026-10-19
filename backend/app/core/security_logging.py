"""Redact credentials before Python log handlers, including SDK/access/error logs."""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from backend.app.config import Settings

_lock = threading.RLock()
_secrets: tuple[str, ...] = ()
_installed = False
_patterns = (
    (re.compile(r"(?i)\bbearer(?:\s+|\.)[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?<=://)[^/@\s:]+:[^/@\s]+@"), "[REDACTED]@"),
    # Drop URL query contents, including percent-encoded credential names/values.
    (re.compile(r"((?:https?://|/)[^\s?\"']*)\?[^\s\"']+"), r"\1?[REDACTED]"),
    (
        re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|password|secret)=)[^&#\s\"']+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{30,}|"
            r"gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{35,})"
        ),
        "[REDACTED]",
    ),
)


def redact_secrets(value: str) -> str:
    for secret in _secrets:
        value = value.replace(secret, "[REDACTED]")
    for pattern, replacement in _patterns:
        value = pattern.sub(replacement, value)
    return value


def _argument(value: object, depth: int = 0) -> object:
    if depth > 4:
        return "[nested log value]"
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, tuple):
        return tuple(_argument(item, depth + 1) for item in value)
    if isinstance(value, list):
        return [_argument(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"(?i)authorization|api[_-]?key|password|secret|token", str(key))
            else _argument(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, BaseException):
        return redact_secrets(str(value))
    return value


def install_log_redaction(settings: Settings) -> None:
    """Install once; retain redaction for concurrent apps and credentials rotated in-process."""
    global _installed, _secrets
    values = {
        secret.get_secret_value()
        for secret in (
            settings.api_token,
            settings.openai_api_key,
            settings.gemini_api_key,
            settings.anthropic_api_key,
            *(secret for server in settings.mcp_servers for secret in server.env.values()),
            *(secret for server in settings.mcp_servers for secret in server.headers.values()),
        )
        if secret is not None
    }
    values.update(
        value
        for key, value in os.environ.items()
        if re.search(
            r"(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?|ACCESS_KEY)(?:_|$)", key
        )
    )
    with _lock:
        _secrets = tuple(sorted(set(_secrets) | (values - {""}), key=len, reverse=True))
        if _installed:
            return
        original = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = original(*args, **kwargs)
            if isinstance(record.args, tuple):
                record.args = tuple(_argument(item) for item in record.args)
            elif isinstance(record.args, dict):
                record.args = {
                    key: "[REDACTED]"
                    if re.search(r"(?i)authorization|api[_-]?key|password|secret|token", str(key))
                    else _argument(item)
                    for key, item in record.args.items()
                }
            # Uvicorn's access formatter needs its five typed positional arguments.
            # Other formatters can receive a fully interpolated/redacted message.
            if record.name != "uvicorn.access":
                record.msg = redact_secrets(record.getMessage())
                record.args = ()
            if record.exc_info:
                record.exc_text = redact_secrets(
                    logging.Formatter().formatException(record.exc_info)
                )
                record.exc_info = None
            if record.stack_info:
                record.stack_info = redact_secrets(record.stack_info)
            return record

        logging.setLogRecordFactory(factory)
        _installed = True
