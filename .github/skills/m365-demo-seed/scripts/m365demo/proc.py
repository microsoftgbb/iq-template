"""Process helpers: is a process alive, and start one detached from this terminal."""

import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
# Handles of children started here, kept so they are not reported as leaked while this process lives.
DETACHED = []


def alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes  # noqa: PLC0415 - Windows only

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def spawn_detached(arguments, log_path, env=None):
    """Start `python <arguments>` in its own session, output to log_path; return its process id.

    The child survives the end of the calling terminal command and of the Copilot tool call.
    """
    command = [sys.executable, *[str(argument) for argument in arguments]]
    with open(log_path, "ab") as log:
        options = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "env": env, "close_fds": True}
        if IS_WINDOWS:
            options["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                        | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            options["start_new_session"] = True
        child = subprocess.Popen(command, **options)
    DETACHED.append(child)
    return child.pid
