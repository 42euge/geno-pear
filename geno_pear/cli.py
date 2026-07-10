#!/usr/bin/env python3
"""pear — file-watch companion with ///command support.

    pear watch <file> [--exec "<cmd>"] [--interval S]

Watches <file> for saves. On each change:
  1. Detects ///command-name lines and executes them from .commands/ registry
  2. Runs --exec if provided

The .commands/ registry lives next to the watched file (or at the vault root).
Create commands in Obsidian by adding <name>.md files with a ## Run section.
"""

import argparse
import subprocess
import sys

from .watch import watch


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("pear — file-watch companion (library + CLI)")
        print("  pear watch <file> [--exec \"<cmd>\"] [--interval S]")
        print("")
        print("  ///commands: type ///name in the watched file and save to trigger")
        print("  commands are defined in .commands/<name>.md alongside the file")
        return 0
    if argv[0] != "watch":
        raise SystemExit(f"Unknown command '{argv[0]}'. Try: pear watch <file>")
    p = argparse.ArgumentParser(prog="pear watch", add_help=False)
    p.add_argument("file")
    p.add_argument("--exec", dest="cmd", default=None)
    p.add_argument("--interval", type=float, default=1.0)
    a = p.parse_args(argv[1:])

    from .commands import process_commands, init_commands_dir, find_commands_dir
    import time as _t

    commands_dir = find_commands_dir(a.file)
    init_commands_dir(commands_dir)

    print(f"watching {a.file}  (Ctrl-C to stop)")
    print(f"  .commands/: {commands_dir}")
    if a.cmd:
        print(f"  --exec: {a.cmd}")

    def on_change(p):
        ts = _t.strftime("%H:%M:%S")
        results = process_commands(p, log=lambda s: print(s))
        if results:
            print(f"  {ts} {len(results)} command(s) executed")
        elif a.cmd:
            subprocess.run(a.cmd, shell=True)
        else:
            print(f"  {ts} saved")

    try:
        watch(a.file, on_change, interval=a.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
