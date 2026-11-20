"""Is the expander actually able to work? Backs the banner in the GUI (the Linux counterpart of
the macOS "Accessibility permission" banner)."""

from __future__ import annotations

import glob
import grp
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

SERVICE = "keyflow.service"


@dataclass
class Problem:
    message: str
    #: Label of a button that can fix it from the GUI (None = the user must act in a terminal).
    action: Optional[str] = None
    #: Shell command shown for fixes that need root.
    command: Optional[str] = None


@dataclass
class Diagnosis:
    problems: list[Problem] = field(default_factory=list)
    service_active: Optional[bool] = None

    @property
    def ok(self) -> bool:
        return not self.problems


def in_input_group() -> bool:
    try:
        gid = grp.getgrnam("input").gr_gid
    except KeyError:
        return True  # no such group on this system: nothing to tell the user
    return gid in os.getgroups() or gid == os.getgid()


def service_is_active() -> Optional[bool]:
    """True/False from systemd, None if there is no systemd user session to ask."""
    if not shutil.which("systemctl"):
        return None
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", SERVICE], capture_output=True,
                           text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    if out in ("active", "activating"):
        return True
    if out in ("inactive", "failed", "deactivating"):
        return False
    return None  # e.g. "unknown" when the unit isn't installed


def start_service() -> Optional[str]:
    """Enable and start the user service. Returns an error message, or None on success."""
    try:
        r = subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    return None if r.returncode == 0 else (r.stderr.strip() or "systemctl failed")


def diagnose() -> Diagnosis:
    d = Diagnosis()
    if not in_input_group():
        d.problems.append(Problem(
            "KeyFlow can't read your keyboard: your account isn't in the \"input\" group (or you "
            "haven't logged in again since being added).",
            command="sudo usermod -aG input $USER   # then log out and back in"))
        return d  # nothing else matters until this is fixed
    uinput = "/dev/uinput"
    if not os.path.exists(uinput):
        d.problems.append(Problem("KeyFlow can't type expansions: /dev/uinput is missing.",
                                  command="sudo modprobe uinput"))
    elif not os.access(uinput, os.W_OK):
        d.problems.append(Problem(
            "KeyFlow can't type expansions: /dev/uinput isn't writable by your account.",
            command="sudo udevadm control --reload && sudo udevadm trigger /dev/uinput"))
    elif not any(os.access(p, os.R_OK) for p in glob.glob("/dev/input/event*")):
        d.problems.append(Problem("KeyFlow can't see any input devices."))

    d.service_active = service_is_active()
    if d.service_active is False and not d.problems:
        d.problems.append(Problem("The KeyFlow service isn't running, so nothing will expand.",
                                  action="Start service"))
    return d
