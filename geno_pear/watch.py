"""File-watch mechanism — the reusable core behind geno-pear's companion.

geno-pear's skill watches a file (via the Monitor tool) and reacts to each
change. This module exposes that same mtime-poll loop as a plain library
function so any geno tool can reuse it — e.g. geno-vault's `vault watch` calls
`geno_pear.watch(registry, on_change=commit)`. Pure stdlib.
"""

import os
import time


def mtime(path: str) -> float | None:
    """The file's modification time, or None if it doesn't exist."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def watch(path, on_change, interval: float = 1.0, initial: bool = False) -> None:
    """Block, calling ``on_change(path)`` every time ``path``'s mtime changes.

    Return ``False`` from ``on_change`` to stop watching. Set ``initial=True``
    to fire once at start. This is geno-pear's watch loop, as a library.
    """
    p = str(path)
    last = mtime(p)
    if initial and on_change(p) is False:
        return
    while True:
        cur = mtime(p)
        if cur is not None and cur != last:
            last = cur
            if on_change(p) is False:
                return
        time.sleep(interval)
