#!/usr/bin/env python3
"""Preserve Multica's process group and hold a shared lease until the CLI exits."""
import os
import signal
import subprocess
import sys

from common import ROOT, PROVIDERS, current_version, lock, release_path, require


def launch(root, provider, arguments):
    require(provider in PROVIDERS, 'Unknown provider')
    with lock(root / provider / 'in-use.lock') as fd:
        selected = current_version(root, provider)
        executable = release_path(root, provider, selected) / 'bin' / provider
        require(executable.is_file() and not executable.is_symlink(), 'Missing native executable')
        # No new session/process group: Multica cancels the entire group it owns.
        # The parent also retains the fd if a CLI closes inherited descriptors.
        child = subprocess.Popen([str(executable), *arguments], pass_fds=(fd,))
        previous = {}

        def forward(signum, _frame):
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

        try:
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                previous[signum] = signal.signal(signum, forward)
            code = child.wait()
            return code if code >= 0 else 128 - code
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


if __name__ == '__main__':
    try:
        require(len(sys.argv) >= 2, 'Missing provider')
        raise SystemExit(launch(ROOT, sys.argv[1], sys.argv[2:]))
    except (ValueError, OSError) as error:
        print('GA401 CLI launcher: ' + str(error), file=sys.stderr)
        raise SystemExit(126)
