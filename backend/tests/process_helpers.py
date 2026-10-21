"""Non-destructive process-liveness checks for native cancellation tests."""

import csv
import os
import subprocess


def process_running(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) is not a portable Windows liveness probe.
        output = subprocess.check_output(
            ["tasklist.exe", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return any(len(row) > 1 and row[1] == str(pid) for row in csv.reader(output.splitlines()))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
