"""geno-pear — pair-programming companion + reusable file-watch library.

The companion is a Claude Code skill (/gt-pear); this package exposes its
underlying watch mechanism as a library so the rest of the geno ecosystem can
compose it (e.g. geno-vault). `from geno_pear import watch`

/// in-context commands:
  When the watch loop sees ///command-name in a saved markdown file it looks up
  .commands/command-name.md in the vault, runs the ## Run bash snippet, and
  removes the trigger line. Commands are created/edited in Obsidian directly.
"""

__version__ = "0.7.0"

from .watch import watch, mtime
from .commands import process_commands, find_commands_dir, init_commands_dir

__all__ = ["watch", "mtime", "process_commands", "find_commands_dir",
           "init_commands_dir", "__version__"]
