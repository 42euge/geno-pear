"""geno-pear — pair-programming companion + reusable file-watch library.

The companion is a Claude Code skill (/gt-pear); this package exposes its
underlying watch mechanism as a library so the rest of the geno ecosystem can
compose it (e.g. geno-vault). `from geno_pear import watch`."""

__version__ = "0.2.0"

from .watch import watch, mtime

__all__ = ["watch", "mtime", "__version__"]
