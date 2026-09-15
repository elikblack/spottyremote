#!/usr/bin/env python3
"""Install or remove Spotty Server as a macOS LaunchAgent.

The generated LaunchAgent runs spotty_supervisor.py, restarts it automatically,
and captures stdout/stderr in ~/Library/Logs.
"""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path


LABEL = "com.elistuff.spottyserver"
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SUPERVISOR = SCRIPT_DIR / "spotty_supervisor.py"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
LOG_DIR = Path.home() / "Library" / "Logs"
STDOUT_LOG = LOG_DIR / "SpottyServer.log"
STDERR_LOG = LOG_DIR / "SpottyServer.err.log"
DOMAIN = "gui/{}".format(os.getuid())
SERVICE = "{}/{}".format(DOMAIN, LABEL)


def run_launchctl(*args, check=False):
    return subprocess.run(
        ["launchctl"] + list(args),
        check=check,
        text=True,
    )


def environment_for_agent():
    env = {"PYTHONUNBUFFERED": "1"}
    for name in (
        "SPOTTY_HOST",
        "SPOTTY_PORT",
        "SPOTIFY_CLIENT_ID",
        "SPOTTY_HEALTH_INTERVAL",
        "SPOTTY_HEALTH_TIMEOUT",
        "SPOTTY_STARTUP_GRACE",
        "SPOTTY_HEALTH_FAILURES",
        "SPOTTY_MAX_RESTART_DELAY",
        "SPOTTY_STABLE_UPTIME",
    ):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def install():
    if sys.platform != "darwin":
        raise SystemExit("This installer is for macOS launchd.")
    if not SUPERVISOR.exists():
        raise SystemExit("Supervisor not found: {}".format(SUPERVISOR))

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Remove an older loaded copy before replacing the plist. Ignore errors when
    # the agent has never been installed or is already unloaded.
    run_launchctl("bootout", DOMAIN, str(PLIST_PATH))

    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-u", str(SUPERVISOR)],
        "WorkingDirectory": str(REPO_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "EnvironmentVariables": environment_for_agent(),
        "StandardOutPath": str(STDOUT_LOG),
        "StandardErrorPath": str(STDERR_LOG),
    }

    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)

    run_launchctl("bootstrap", DOMAIN, str(PLIST_PATH), check=True)
    run_launchctl("enable", SERVICE)
    run_launchctl("kickstart", "-k", SERVICE, check=True)

    print("Installed and started {}".format(LABEL))
    print("  plist:  {}".format(PLIST_PATH))
    print("  stdout: {}".format(STDOUT_LOG))
    print("  stderr: {}".format(STDERR_LOG))
    print("  status: launchctl print {}".format(SERVICE))


def uninstall():
    if sys.platform != "darwin":
        raise SystemExit("This installer is for macOS launchd.")

    run_launchctl("bootout", DOMAIN, str(PLIST_PATH))
    try:
        PLIST_PATH.unlink()
    except FileNotFoundError:
        pass
    print("Uninstalled {}".format(LABEL))


def status():
    if sys.platform != "darwin":
        raise SystemExit("This command is for macOS launchd.")
    result = run_launchctl("print", SERVICE)
    raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--uninstall", action="store_true", help="stop and remove the LaunchAgent")
    group.add_argument("--status", action="store_true", help="show launchd service status")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
    elif args.status:
        status()
    else:
        install()


if __name__ == "__main__":
    main()
