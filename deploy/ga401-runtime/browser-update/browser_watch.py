"""Reuse the existing maintenance services without changing provider update policy."""
import argparse
import json
import os
from pathlib import Path
import signal
import time

from browser_common import ROOT, RESULTS, read_json, require


def cycle(role, force=False):
    actions = []
    if role == 'updater':
        import updater as provider_updater
        import browser_updater
        actions = [('providers', lambda: provider_updater.tick(force=force)),
                   ('browser', lambda: browser_updater.tick(force=force))]
    else:
        import browser_validator
        if role == 'validator':
            import validator as provider_validator
            actions.append(('providers', lambda: provider_validator.tick(force=force)))
        consumer = 'login' if role == 'login-validator' else 'runtime'
        actions.append(('browser', lambda: browser_validator.tick(consumer, force=force)))
    failed = False
    for name, action in actions:
        try:
            value = action()
            if isinstance(value, list):
                failed |= any(report.get('passed') is False for report in value)
            elif name == 'browser':
                failed |= value.get('status') == 'held-error'
            else:
                failed |= any(item.get('status') == 'held-error' for item in value['providers'].values())
        except Exception as error:
            failed = True
            print(json.dumps({'maintenance': name, 'error': str(error)[:1000]}), flush=True)
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('updater', 'validator', 'login-validator'))
    parser.add_argument('command', choices=('watch', 'once', 'status'), default='watch', nargs='?')
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(read_json(ROOT / 'status.json'), indent=2))
        return
    require(os.getuid() == (1001 if args.role == 'updater' else 1002), 'Wrong browser maintenance UID')
    if args.role != 'updater':
        require(not os.access(ROOT, os.W_OK) and os.access(RESULTS, os.W_OK), 'Unsafe validation mounts')
        require(set(Path('/sys/class/net').iterdir()) == {Path('/sys/class/net/lo')}, 'Validation must be offline')
    def stop(signum, _frame):
        raise SystemExit(128 + signum)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    while True:
        failed = cycle(args.role, force=args.command == 'once')
        if args.command == 'once':
            raise SystemExit(int(failed))
        time.sleep(60)


if __name__ == '__main__':
    main()
