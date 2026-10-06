"""Raises a console event at an idle ``python -m tftp serve|relay`` (Windows only).

A console control event goes to every process on the console, so the test
that wants one for the command under test cannot raise it from its own
process. It runs this script on a console of its own (``CREATE_NO_WINDOW``);
the script starts the command, waits until it reports that it is listening,
raises the event and reports how the command ended, as JSON in the file named
by the last argument.

    python ctrlc_driver.py serve|relay ctrl-c|ctrl-break REPORT
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(command: str, event: str, report: str) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    # Ctrl-C processing may be disabled in what started this process, and that
    # is inherited by the child: enable it first.
    kernel32.SetConsoleCtrlHandler(None, 0)
    signal.signal(signal.SIGINT, lambda *_: None)
    signal.signal(signal.SIGBREAK, lambda *_: None)

    work = tempfile.mkdtemp(prefix="ctrlc_")
    port = free_port()
    if command == "serve":
        argv = [sys.executable, "-m", "tftp", "serve", work, "-l", "127.0.0.1", "-p", str(port)]
        ready = "serving"
    else:
        argv = [sys.executable, "-m", "tftp", "relay", "127.0.0.1:9", "-l", "127.0.0.1", "-p", str(port)]
        ready = "relaying"
    err_path = os.path.join(work, "err")
    marks = {"command": command, "event": event}
    with open(err_path, "wb") as err:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL)
        start = time.monotonic()
        while time.monotonic() - start < 20 and proc.poll() is None:
            with open(err_path, "rb") as fh:
                if ready.encode() in fh.read():
                    break
            time.sleep(0.05)
        time.sleep(1.0)  # idle: nothing has been sent to the port
        marks["alive_before_event"] = proc.poll() is None
        sent = time.monotonic()
        # Group 0 is this console: this process and the command, and nothing else.
        kernel32.GenerateConsoleCtrlEvent(1 if event == "ctrl-break" else 0, 0)
        try:
            proc.wait(10)
            marks["exited_after"] = round(time.monotonic() - sent, 2)
        except subprocess.TimeoutExpired:
            marks["exited_after"] = None
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            proc.wait(10)
        marks["returncode"] = proc.returncode
    with open(err_path, "rb") as fh:
        marks["stderr"] = fh.read().decode("utf-8", "replace")
    with open(report, "w") as fh:
        json.dump(marks, fh)


if __name__ == "__main__":
    main(*sys.argv[1:4])
