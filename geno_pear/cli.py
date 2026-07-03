#!/usr/bin/env python3
"""pear — thin CLI over the geno-pear watch library.

    pear watch <file> --exec "<cmd>"   run <cmd> each time <file> changes

The rich companion (personas, sidecar feedback) is the /gt-pear skill; this CLI
just exposes the mechanism for scripts/other tools.
"""

import argparse
import subprocess
import sys

from .watch import watch


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("pear — file-watch companion (library + CLI)")
        print("  pear watch <file> --exec \"<cmd>\" [--interval S]")
        return 0
    if argv[0] != "watch":
        raise SystemExit(f"Unknown command '{argv[0]}'. Try: pear watch <file> --exec ...")
    p = argparse.ArgumentParser(prog="pear watch", add_help=False)
    p.add_argument("file")
    p.add_argument("--exec", dest="cmd", required=True)
    p.add_argument("--interval", type=float, default=1.0)
    a = p.parse_args(argv[1:])
    print(f"watching {a.file} → running: {a.cmd}  (Ctrl-C to stop)")
    try:
        watch(a.file, lambda _p: subprocess.run(a.cmd, shell=True), interval=a.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
