# SPDX-License-Identifier: Apache-2.0
"""Bounded parser transport, including a macOS resident-memory watchdog.

Darwin does not accept the worker's RLIMIT_AS setting. Do not silently drop the
memory guard: sample RSS from the parent instead, with CPU/wall/input/output limits
still applied. This is a sampled resident-memory limit, NOT a hard address-space
sandbox. A parser can briefly exceed it between samples (at most 50 ms nominally).
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time

from flora.support.errors import ValidationError

MEMORY_LIMIT = 512 * 1024 * 1024


def _darwin_memory_reader():
    # Public rusage_info_v0 from the macOS SDK's sys/resource.h. No shell or
    # credentials, and only the owned worker PID is inspected.
    class Usage(ctypes.Structure):
        _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
            (name, ctypes.c_uint64)
            for name in (
                "user_time",
                "system_time",
                "pkg_idle_wkups",
                "interrupt_wkups",
                "pageins",
                "wired_size",
                "resident_size",
                "phys_footprint",
                "proc_start_abstime",
                "proc_exit_abstime",
            )
        ]

    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        read = library.proc_pid_rusage
        read.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        read.restype = ctypes.c_int
    except (OSError, AttributeError):
        raise ValidationError("Document memory watchdog is unavailable on this Mac") from None

    def resident_bytes(pid):
        usage = Usage()
        if read(pid, 0, ctypes.byref(usage)) != 0:
            raise ValidationError("Document memory watchdog could not inspect the worker")
        return max(usage.resident_size, usage.phys_footprint)

    return resident_bytes


def run_worker(payload, environment, *, timeout=30):
    command = [sys.executable, "-m", "flora.general._document_worker"]
    options = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "env": environment,
        "close_fds": True,
        "start_new_session": True,
    }
    if sys.platform != "darwin":
        return subprocess.run(command, input=payload, timeout=timeout, check=False, **options)
    # Initialize before spawning: unsupported monitoring never runs unguarded.
    memory = _darwin_memory_reader()
    with subprocess.Popen(command, stdin=subprocess.PIPE, **options) as process:
        deadline = time.monotonic() + timeout
        pending = payload
        first_attempt = True
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                if process.poll() is None:
                    try:
                        used = memory(process.pid)
                    except ValidationError:
                        if process.poll() is None:
                            raise
                    else:
                        if used > MEMORY_LIMIT:
                            raise ValidationError(
                                "Document parser exceeded the 512 MiB resident-memory limit"
                            )
                try:
                    # Give the first communicate call enough time to drain
                    # the bounded request into the worker pipe. A very short
                    # Darwin timeout can interrupt that write; retrying with
                    # ``input=None`` then leaves the child waiting for EOF
                    # and the parser appears to hang until the wall deadline.
                    window = min(0.5 if first_attempt else 0.05, remaining)
                    stdout, _ = process.communicate(input=pending, timeout=window)
                    return subprocess.CompletedProcess(command, process.returncode, stdout)
                except subprocess.TimeoutExpired:
                    pending = None  # communicate retains unsent input across timeout retries.
                    first_attempt = False
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
