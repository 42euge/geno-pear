"""/// in-context command system for geno-pear.

## Protocol

Draft with `///`, commit with `////`.

  ///sync-jira          ← draft — ignored until committed
  ///new-cc ngrt.main   ← another draft
  ////                  ← commit all pending /// above

  ///focus ngrt.ct.deploy
  ////focus             ← commit only the focus command above

When `////` (or `////command`) is detected on save the watcher:
  1. Replaces it with `// heard @ HH:MM:SS //` in the file
  2. Runs the matched commands
  3. Updates status every 2s: `// heard @ HH:MM:SS - running sync-jira... //`
  4. Finishes with `// done @ HH:MM:SS //`
  5. User deletes the `// done //` line — watcher sees it gone and knows ACK'd

Freeform text (no ///command match) between `///` and `////` is passed to the
LLM as context for the `geno-tasks watch` reconciler.

Command registry: .commands/<name>.md with a `## Run` bash section.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

# ///command-name optional args  (draft lines — ignored until committed)
_DRAFT_RE = re.compile(r"^///([a-zA-Z0-9_-]+)([ \t]+[^\n]*)?\s*$", re.MULTILINE)

# //// commit all pending commands above this line
_COMMIT_ALL_RE = re.compile(r"^////\s*$", re.MULTILINE)

# ////command-name  commit only that specific command
_COMMIT_ONE_RE = re.compile(r"^////([a-zA-Z0-9_-]+)\s*$", re.MULTILINE)

# // heard ... // and // done // — status markers written by the watcher
_STATUS_RE = re.compile(r"^// (heard|done|error)[^\n]*//\s*$", re.MULTILINE)

# ```bash...``` in a command definition
_RUN_RE = re.compile(r"## Run\s*```(?:bash|sh)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def find_commands_dir(watched_file: str | Path) -> Path:
    p = Path(watched_file).resolve()
    for parent in [p.parent] + list(p.parents):
        if (parent / ".commands").exists():
            return parent / ".commands"
        if (parent / "workflow").exists():
            return parent / ".commands"
        if parent.name == "tasks" and parent.parent.name == ".geno":
            return parent / ".commands"
    return p.parent / ".commands"


def init_commands_dir(commands_dir: Path) -> None:
    commands_dir.mkdir(parents=True, exist_ok=True)
    readme = commands_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Commands\n\n"
            "Draft with `///`, commit with `////`.\n\n"
            "```\n"
            "///sync-jira\n"
            "////            ← runs sync-jira\n\n"
            "///focus ngrt.ct.deploy\n"
            "////focus       ← runs only focus\n"
            "```\n\n"
            "Create commands by adding `<name>.md` files here with a `## Run` bash block.\n",
            encoding="utf-8",
        )


def load_command(commands_dir: Path, name: str) -> str | None:
    cmd_file = commands_dir / f"{name}.md"
    if not cmd_file.exists():
        return None
    text = cmd_file.read_text(encoding="utf-8", errors="replace")
    m = _RUN_RE.search(text)
    return m.group(1).strip() if m else None


def _patch_file(file_path: Path, old: str, new: str) -> None:
    """Replace old with new in file, with a short retry on race."""
    for _ in range(3):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            if old in text:
                file_path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return
        except OSError:
            time.sleep(0.1)


def _set_status(file_path: Path, commit_line: str, status: str) -> None:
    """Update the heard/status line in the file."""
    _patch_file(file_path, commit_line, status)


def execute_command(script: str, args: str = "", cwd: str | None = None) -> tuple[int, str]:
    full = script.replace("$ARGS", args).replace("${ARGS}", args)
    r = subprocess.run(full, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.returncode, (r.stdout + r.stderr).strip()


def _run_with_feedback(
    file_path: Path,
    commit_line: str,
    commands: list[tuple[str, str]],
    commands_dir: Path,
    freeform: str,
    log=print,
) -> None:
    """Execute commands with inline file status updates."""
    ts = time.strftime("%H:%M:%S")
    heard_line = f"// heard @ {ts} //"
    _set_status(file_path, commit_line, heard_line)

    results = []
    for name, args in commands:
        script = load_command(commands_dir, name)
        if script is None:
            msg = f"// heard @ {ts} - ⚠ {name} not found //"
            _set_status(file_path, heard_line, msg)
            heard_line = msg
            log(f"  ⚠ ///{name} — not in {commands_dir}")
            results.append(f"MISSING:{name}")
            continue

        running_msg = f"// heard @ {ts} - running {name}... //"
        _set_status(file_path, heard_line, running_msg)
        heard_line = running_msg
        log(f"  ▶ ///{name} {args}".rstrip())

        # Ticker: update status every 2s while running
        done_event = threading.Event()
        tick_count = [0]

        def _tick():
            while not done_event.wait(2):
                tick_count[0] += 1
                nonlocal heard_line
                msg = f"// heard @ {ts} - {name} ({tick_count[0]*2}s)... //"
                _set_status(file_path, heard_line, msg)
                heard_line = msg

        ticker = threading.Thread(target=_tick, daemon=True)
        ticker.start()

        rc, out = execute_command(script, args=args, cwd=str(file_path.parent))
        done_event.set()
        ticker.join(timeout=0.5)

        status = "✓" if rc == 0 else f"✗ exit {rc}"
        if out:
            for line in out.splitlines()[:3]:
                log(f"    {line}")
        log(f"    {status} {name}")
        results.append(f"{status}:{name}")

        # Remove the original ///name draft line
        _patch_file(
            file_path,
            "",  # handled below holistically
            "",
        )

    # Remove all processed draft lines + replace status with done
    text = file_path.read_text(encoding="utf-8", errors="replace")
    for name, args in commands:
        pattern = rf"^///{re.escape(name)}[ \t]*[^\n]*\n?"
        text = re.sub(pattern, "", text, flags=re.MULTILINE)
    if freeform.strip():
        log(f"  📝 freeform: {freeform[:60].strip()}")
    # Replace current status line with done
    done_ts = time.strftime("%H:%M:%S")
    text = re.sub(r"^// heard[^\n]*//\s*$",
                  f"// done @ {done_ts} //", text, flags=re.MULTILINE)
    file_path.write_text(text, encoding="utf-8")
    log(f"  ✓ done — delete '// done @ {done_ts} //' to acknowledge")


def check_commit(file_path: str | Path, log=print) -> bool:
    """Check for //// or ////command in file. If found, process and return True."""
    p = Path(file_path)
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8", errors="replace")

    # Don't re-trigger while a status marker is still in the file
    if _STATUS_RE.search(content):
        return False

    commands_dir = find_commands_dir(p)
    init_commands_dir(commands_dir)

    # Check for ////command (targeted commit — process only that one)
    m_one = _COMMIT_ONE_RE.search(content)
    if m_one:
        commit_line = m_one.group(0).rstrip()
        name = m_one.group(1)
        # Find the matching ///name line
        m_draft = re.search(rf"^///{re.escape(name)}([ \t]+[^\n]*)?\s*$",
                            content, re.MULTILINE)
        args = (m_draft.group(1) or "").strip() if m_draft else ""
        log(f"  ◉ commit: ///{name}")
        _run_with_feedback(p, commit_line, [(name, args)], commands_dir, "", log)
        return True

    # Check for //// (commit all)
    m_all = _COMMIT_ALL_RE.search(content)
    if m_all:
        commit_line = m_all.group(0).rstrip()
        # Collect all ///command lines above the commit
        commit_pos = m_all.start()
        before = content[:commit_pos]
        commands = []
        freeform_lines = []
        for line in before.splitlines():
            dm = re.match(r"^///([a-zA-Z0-9_-]+)([ \t]+[^\n]*)?$", line)
            if dm:
                commands.append((dm.group(1), (dm.group(2) or "").strip()))
            elif line.strip() and not line.startswith("#") and not line.startswith("---"):
                freeform_lines.append(line)
        freeform = "\n".join(freeform_lines[-10:])  # last 10 freeform lines
        if not commands and not freeform.strip():
            # Nothing to do — remove the //// and leave
            _patch_file(p, commit_line + "\n", "")
            return False
        count = len(commands)
        log(f"  ◉ commit all: {count} command(s)")
        _run_with_feedback(p, commit_line, commands, commands_dir, freeform, log)
        return True

    return False


# Legacy compat: process_commands now just delegates to check_commit
def process_commands(file_path: str | Path, log=print) -> list[str]:
    check_commit(file_path, log=log)
    return []
