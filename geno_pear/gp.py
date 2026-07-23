#!/usr/bin/env python3
"""gp — short-form file watcher for geno-pear.

Run it from inside the directory you're working in and point it at a file:

    gp overview.md
    gp notes/todo.md --exec "make build"
    gp overview.md --interval 0.5

Equivalent to `pear watch <file>` but with no subcommand — the common case is
"watch this one file in the directory I'm standing in". The file path is
resolved relative to the current working directory.

On each save it:
  1. Detects ///command-name lines and runs them from the .commands/ registry
     (found next to the file or at the vault root), then
  2. Runs --exec if provided.
"""

import argparse
import os
import subprocess
import sys

from .watch import watch


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)

    p = argparse.ArgumentParser(
        prog="gp",
        description="Watch a file for saves and run its ///commands (geno-pear).",
    )
    p.add_argument("file", help="file to watch, relative to the current directory (e.g. overview.md)")
    p.add_argument("--exec", dest="cmd", default=None,
                   help="shell command to run on each change (after ///commands)")
    p.add_argument("--interval", type=float, default=1.0,
                   help="poll interval in seconds (default: 1.0)")
    a = p.parse_args(argv)

    # Resolve relative to CWD so `gp overview.md` targets the current directory.
    target = os.path.abspath(a.file)
    if not os.path.exists(target):
        print(f"gp: no such file: {a.file} (looked in {os.getcwd()})", file=sys.stderr)
        return 1

    from .commands import check_commit, init_commands_dir, find_commands_dir

    commands_dir = find_commands_dir(target)
    init_commands_dir(commands_dir)

    print(f"gp: watching {a.file}  (Ctrl-C to stop)")
    print(f"  .commands/: {commands_dir}")
    print(f"  draft: ///command   commit: ////  or  ////command")
    if a.cmd:
        print(f"  --exec: {a.cmd}")

    def on_change(path):
        triggered = check_commit(path, log=print)
        if not triggered and a.cmd:
            subprocess.run(a.cmd, shell=True)

    try:
        watch(target, on_change, interval=a.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
