"""/// in-context command detection and execution for geno-pear.

When the watch loop detects a `///command-name` line in a saved markdown file it:
  1. Looks up the command in the vault's .commands/ registry
  2. Reads the command definition (a markdown file with a ## Run section)
  3. Executes the shell snippet from the ## Run section
  4. Removes the ///command line from the file (consumed once)

Command registry lives in <vault_root>/.commands/ alongside the watched file.
Each command is a markdown file: .commands/<name>.md

Example .commands/sync-jira.md:
  ---
  name: sync-jira
  description: Pull latest Jira data into task files
  ---
  # sync-jira
  ## Run
  ```bash
  geno-tasks sync
  ```

The .commands/ folder is Obsidian-visible so you can browse, create, and edit
commands directly in Obsidian.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Matches ///command-name (with optional trailing space/args)
_CMD_RE = re.compile(r"^///([a-zA-Z0-9_-]+)([ \t]+.*)?\s*$", re.MULTILINE)

# Matches the first ```bash ... ``` block in a command definition file
_RUN_RE = re.compile(r"## Run\s*```(?:bash|sh)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def find_commands_dir(watched_file: str | Path) -> Path:
    """Find the .commands/ directory relative to the watched file."""
    p = Path(watched_file).resolve()
    # Walk up to find a directory that looks like a vault root
    # (has workflow/ or .commands/ or is ~/.geno/tasks/)
    for parent in [p.parent] + list(p.parents):
        if (parent / ".commands").exists():
            return parent / ".commands"
        if (parent / "workflow").exists():
            return parent / ".commands"  # will be created
        if parent.name == "tasks" and (parent.parent.name == ".geno"):
            return parent / ".commands"
    return p.parent / ".commands"


def detect_commands(content: str) -> list[tuple[str, str]]:
    """Return list of (command_name, args) tuples found as ///name lines."""
    return [(m.group(1), (m.group(2) or "").strip()) for m in _CMD_RE.finditer(content)]


def load_command(commands_dir: Path, name: str) -> str | None:
    """Load the shell script from a command definition file. Returns None if not found."""
    cmd_file = commands_dir / f"{name}.md"
    if not cmd_file.exists():
        return None
    text = cmd_file.read_text(encoding="utf-8", errors="replace")
    m = _RUN_RE.search(text)
    return m.group(1).strip() if m else None


def consume_command(file_path: str | Path, name: str) -> None:
    """Remove the ///name line from the file after it has been executed."""
    p = Path(file_path)
    text = p.read_text(encoding="utf-8", errors="replace")
    cleaned = re.sub(rf"^///{re.escape(name)}[ \t].*\n?", "", text, flags=re.MULTILINE)
    if cleaned != text:
        p.write_text(cleaned, encoding="utf-8")


def execute_command(script: str, args: str = "", cwd: str | None = None) -> tuple[int, str]:
    """Run a shell script, interpolating $ARGS with the provided args string."""
    full = script.replace("$ARGS", args).replace("${ARGS}", args)
    r = subprocess.run(full, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.returncode, (r.stdout + r.stderr).strip()


def init_commands_dir(commands_dir: Path) -> None:
    """Create .commands/ with a README if it doesn't exist."""
    commands_dir.mkdir(parents=True, exist_ok=True)
    readme = commands_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Commands\n\n"
            "Each `.md` file in this folder defines a `///command` you can trigger\n"
            "from any watched markdown file (like `overview.md`).\n\n"
            "## Creating a command\n\n"
            "Create `<name>.md` with a `## Run` section containing a bash snippet:\n\n"
            "```markdown\n"
            "---\n"
            "name: my-command\n"
            "description: What this command does\n"
            "---\n\n"
            "# my-command\n\n"
            "## Run\n"
            "```bash\n"
            "echo hello from my-command\n"
            "```\n"
            "```\n\n"
            "Then type `///my-command` on any line of `overview.md` and save.\n",
            encoding="utf-8",
        )


def process_commands(file_path: str | Path, log=print) -> list[str]:
    """Detect, execute, and consume all ///commands in a file. Returns list of results."""
    p = Path(file_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8", errors="replace")
    found = detect_commands(content)
    if not found:
        return []

    commands_dir = find_commands_dir(p)
    init_commands_dir(commands_dir)
    results = []

    for name, args in found:
        script = load_command(commands_dir, name)
        if script is None:
            log(f"  ⚠ ///  {name} — not found in {commands_dir}")
            log(f"    create {commands_dir}/{name}.md with a ## Run section")
            results.append(f"MISSING: {name}")
            continue
        log(f"  ▶ ///{name} {args}".rstrip())
        rc, out = execute_command(script, args=args, cwd=str(p.parent))
        consume_command(p, name)
        status = "✓" if rc == 0 else f"✗ (exit {rc})"
        if out:
            for line in out.splitlines()[:5]:  # cap at 5 lines
                log(f"    {line}")
        log(f"    {status}")
        results.append(f"{status} {name}")

    return results
