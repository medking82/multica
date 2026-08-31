"""Resolve a generation once: a pointer change cannot mix an active browser's files."""
import argparse
import os
from pathlib import Path

from browser_common import ROOT, check_layout, read_json, release_path, require, selected


def launch_spec(mode, args, directory):
    record = read_json(directory / 'release.json')
    check_layout(directory, record)
    env = dict(os.environ)
    env.update(NODE_PATH=str(directory / 'node_modules') + ':/usr/local/lib/node_modules',
               PLAYWRIGHT_BROWSERS_PATH='0', PLAYWRIGHT_SKIP_BROWSER_GC='1')
    if mode == 'playwright':
        argv = ['node', str(directory / 'node_modules/playwright/cli.js'), *args]
    elif mode == 'mcp':
        argv = ['node', str(directory / 'node_modules/@playwright/mcp/cli.js'),
                '--headless', '--isolated', '--config', '/opt/runtime/browser-config.json',
                '--executable-path', str(directory / record['executable']), *args]
    else:
        require(mode == 'login' and not args, 'Unknown browser launch mode')
        from profile_guard import prepare_profile
        prepare_profile(Path('/home/browser'), record)
        argv = ['node', '/opt/browser/entrypoint.cjs']
    return argv, env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', help='Exact installed generation for offline validation')
    parser.add_argument('mode', choices=('playwright', 'mcp', 'login'))
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    # Direct require('playwright') also resolves Node's module realpath once. Old
    # generations are retained, so lazy helpers and later tabs keep matching files.
    directory = release_path(ROOT, args.generation) if args.generation else selected(ROOT)
    if args.mode == 'login':
        require(os.getuid() == 1000, 'Human browser must run as UID 1000')
    argv, env = launch_spec(args.mode, args.args, directory)
    os.execvpe(argv[0], argv, env)


if __name__ == '__main__':
    main()
