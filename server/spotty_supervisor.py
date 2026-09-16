#!/usr/bin/env python3
"""Keep Spotty Server alive and restart it if it exits or stops responding.

This wrapper intentionally uses only the Python standard library. It is useful
when running Spotty manually, and is also the process installed by the macOS
LaunchAgent helper.
"""

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


SERVER_FILE = Path(__file__).resolve().parent / "spotty_history_server.py"
PORT = int(os.environ.get("SPOTTY_PORT", "8787"))
HEALTH_URL = "http://127.0.0.1:{}/api/health".format(PORT)

CHECK_INTERVAL = float(os.environ.get("SPOTTY_HEALTH_INTERVAL", "10"))
HEALTH_TIMEOUT = float(os.environ.get("SPOTTY_HEALTH_TIMEOUT", "2"))
STARTUP_GRACE = float(os.environ.get("SPOTTY_STARTUP_GRACE", "5"))
FAILURES_BEFORE_RESTART = int(os.environ.get("SPOTTY_HEALTH_FAILURES", "3"))
MAX_RESTART_DELAY = float(os.environ.get("SPOTTY_MAX_RESTART_DELAY", "30"))
STABLE_UPTIME = float(os.environ.get("SPOTTY_STABLE_UPTIME", "60"))

_stopping = False
_child = None


def log(message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print("[{}] supervisor: {}".format(stamp, message), file=sys.stderr, flush=True)


def request_stop(signum, _frame):
    global _stopping
    _stopping = True
    log("received signal {}; shutting down".format(signum))


def health_ok():
    request = urllib.request.Request(
        HEALTH_URL,
        headers={"User-Agent": "SpottySupervisor/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def stop_child(child):
    if child is None or child.poll() is not None:
        return

    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log("server ignored terminate; killing it")
        child.kill()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def sleep_interruptibly(seconds):
    deadline = time.monotonic() + max(0.0, seconds)
    while not _stopping and time.monotonic() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def run():
    global _child

    if not SERVER_FILE.exists():
        log("server file not found: {}".format(SERVER_FILE))
        return 2

    restart_delay = 1.0

    while not _stopping:
        log("starting Spotty Server")
        started_at = time.monotonic()
        try:
            _child = subprocess.Popen(
                [sys.executable, "-u", str(SERVER_FILE)],
                env=os.environ.copy(),
            )
        except OSError as exc:
            log("could not start server: {}".format(exc))
            sleep_interruptibly(restart_delay)
            restart_delay = min(restart_delay * 2, MAX_RESTART_DELAY)
            continue

        health_failures = 0
        next_health_check = started_at + STARTUP_GRACE
        restart_reason = None

        while not _stopping:
            return_code = _child.poll()
            if return_code is not None:
                restart_reason = "server exited with code {}".format(return_code)
                break

            now = time.monotonic()
            if now >= next_health_check:
                if health_ok():
                    health_failures = 0
                else:
                    health_failures += 1
                    log(
                        "health check failed ({}/{})".format(
                            health_failures, FAILURES_BEFORE_RESTART
                        )
                    )
                    if health_failures >= FAILURES_BEFORE_RESTART:
                        restart_reason = "server stopped responding to health checks"
                        break
                next_health_check = now + CHECK_INTERVAL

            time.sleep(0.25)

        uptime = time.monotonic() - started_at

        if _stopping:
            stop_child(_child)
            break

        log("{}; restarting".format(restart_reason or "server stopped"))
        stop_child(_child)

        if uptime >= STABLE_UPTIME:
            restart_delay = 1.0
        else:
            log("restart backoff: {:.1f}s".format(restart_delay))
            sleep_interruptibly(restart_delay)
            restart_delay = min(restart_delay * 2, MAX_RESTART_DELAY)

    _child = None
    log("stopped")
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    raise SystemExit(run())
