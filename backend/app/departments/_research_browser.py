"""Private browser worker; all SDK subprocesses inherit only its sanitized environment."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys

from backend.app.departments._research_web import MAX_SOURCE_BYTES, _browser_html


def main() -> None:
    payload = json.loads(sys.stdin.buffer.read(16384))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"url", "max_bytes"}
        or not isinstance(payload["url"], str)
        or not isinstance(payload["max_bytes"], int)
        or not 0 < payload["max_bytes"] <= MAX_SOURCE_BYTES
    ):
        raise ValueError("Invalid browser worker request")
    # SDK diagnostics never share the protocol stream or expose URLs to service logs.
    with (
        open(os.devnull, "w") as discarded,
        contextlib.redirect_stdout(discarded),
        contextlib.redirect_stderr(discarded),
    ):
        result = asyncio.run(_browser_html(payload["url"], max_bytes=payload["max_bytes"]))
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
