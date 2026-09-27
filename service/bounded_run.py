"""service/bounded_run.py — run a command, and stop it and everything it started at a deadline.

``python service/bounded_run.py SECONDS -- CMD [ARG...]`` runs CMD in a session (and so a
process group) of its own, with this process's stdin, stdout and stderr. When CMD ends in
time, this exits with CMD's status. When it is still running after SECONDS, the whole group
gets SIGTERM, SIGKILL after a 5 s grace, and this exits with 124 (as coreutils ``timeout``
does). A SIGTERM sent to this process is passed on to the group.

Why (atrium-project#53): ``service/enrichment.py`` runs ``run_pipeline.py`` with
``subprocess.run``, whose own ``timeout=`` kills only its direct child. The stage scripts
and their UDPipe, NameTag and KeyBERT processes kept running after the API had answered
504 — holding a CPU, a LINDAT connection and the job's workspace — and the job's slot was
freed before they ended, so MAX_CONCURRENT_JOBS could be exceeded. The command line stays a
``subprocess.run`` call so the service's tests can keep standing in for it.

Standard library, POSIX only (the service image is Linux).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Exit status when the deadline stopped the command.
TIMED_OUT = 124
#: Seconds between SIGTERM and SIGKILL.
GRACE_S = 5.0


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:  # every process of the group has already ended
        pass


def stop_group(proc: subprocess.Popen, grace_s: float = GRACE_S) -> None:
    """SIGTERM the command's process group, then SIGKILL whatever is left of it."""
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    # Also when the leader ended: a stage it started may still be running in its group.
    _signal_group(proc, signal.SIGKILL)
    proc.wait()


def run(seconds: float, cmd: List[str], grace_s: float = GRACE_S) -> int:
    """Run *cmd*; its exit status, or :data:`TIMED_OUT` once it was stopped at *seconds*."""
    proc = subprocess.Popen(cmd, start_new_session=True)
    previous = signal.signal(
        signal.SIGTERM, lambda _sig, _frame: _signal_group(proc, signal.SIGTERM)
    )
    try:
        return proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        logger.warning(
            "still running after %g s (API_JOB_TIMEOUT); stopping it and every process it started.",
            seconds,
        )
        stop_group(proc, grace_s)
        return TIMED_OUT
    finally:
        signal.signal(signal.SIGTERM, previous)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        logger.error("usage: bounded_run.py SECONDS -- CMD [ARG...]")
        return 2
    try:
        seconds = float(args[0])
    except ValueError:
        logger.error("%r is not a number of seconds", args[0])
        return 2
    return run(seconds, args[2:])


if __name__ == "__main__":
    # stderr, which the service relays to its own log (service/enrichment.py's _run_and_log).
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[bounded_run] %(message)s")
    sys.exit(main())
