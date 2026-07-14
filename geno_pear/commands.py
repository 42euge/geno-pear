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


def _load_read_status():
    """Return geno_agents.read_status if geno-agents is installed, else None.

    Agent execution/tracking lives in geno-agents (the `geno-agent` CLI). geno-pear
    only *polls* the registry to mirror status into the watched file, so it depends
    on geno-agents softly — the ///command protocol still works without it, agents
    just won't report live status.
    """
    try:
        from geno_agents import read_status
        return read_status
    except ImportError:
        return None


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


def _replace_line(file_path: Path, old_line: str, new_line: str) -> bool:
    """Replace a WHOLE line (matched exactly, stripped) with new_line.

    Line-anchored — never a substring replace — so a short/growing status
    string can't accidentally match text elsewhere in the file. Returns True
    if a line was replaced.
    """
    old_stripped = old_line.strip()
    for _ in range(3):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")
            for i, line in enumerate(lines):
                if line.strip() == old_stripped:
                    lines[i] = new_line
                    file_path.write_text("\n".join(lines), encoding="utf-8")
                    return True
            return False
        except OSError:
            time.sleep(0.1)
    return False


def _set_status(file_path: Path, commit_line: str, status: str,
                stream: bool = False, char_delay: float = 0.025) -> None:
    """Update the status line in the file (whole-line replace).

    If stream=True, types the status character-by-character so Obsidian renders
    it progressively. Uses line-anchored replacement, so the growing prefix can
    never collide with other text in the file.
    """
    if not stream or len(status) <= 20:
        _replace_line(file_path, commit_line, status)
        return

    prev = commit_line
    for i in range(1, len(status) + 1):
        if not _replace_line(file_path, prev, status[:i]):
            # line vanished (user edited) — stop streaming
            return
        prev = status[:i]
        if i < len(status):
            time.sleep(char_delay)


def execute_command(script: str, args: str = "", cwd: str | None = None) -> tuple[int, str, str | None]:
    """Run a shell script. Returns (returncode, output, agent_id_or_None).

    If the script prints GENO_AGENT_ID=<id> to stdout, the caller should switch
    to agent-polling mode instead of waiting for the subprocess to finish.
    """
    full = script.replace("$ARGS", args).replace("${ARGS}", args)
    r = subprocess.run(full, shell=True, capture_output=True, text=True, cwd=cwd)
    out = (r.stdout + r.stderr).strip()
    # Detect if command launched a tracked agent
    agent_id = None
    for line in out.splitlines():
        if line.startswith("GENO_AGENT_ID="):
            agent_id = line.split("=", 1)[1].strip()
            break
    return r.returncode, out, agent_id


def _poll_agent(file_path: Path, agent_id: str, current_status_line: str,
                command_name: str, log) -> None:
    """Poll the agent registry every 2s and write status back to the file.
    Returns when agent reaches done/error. Does NOT write the final done marker
    here — that happens in the main flow after all commands finish.
    """
    read_status = _load_read_status()
    if read_status is None:
        log(f"    geno-agents not installed — cannot poll agent {agent_id}")
        return

    heard_line = current_status_line
    ts = time.strftime("%H:%M:%S")
    timeout = time.monotonic() + 600  # 10 min max

    while time.monotonic() < timeout:
        data = read_status(agent_id)
        if not data:
            time.sleep(2)
            continue
        status = data.get("status", "running")
        msg = data.get("message", "")[:60]
        new_line = f"// heard @ {ts} - agent {command_name}: {msg} //"
        _set_status(file_path, heard_line, new_line)
        heard_line = new_line
        log(f"    [{status}] {msg}")
        if status in ("done", "error"):
            break
        time.sleep(2)


def _append_status_line(file_path: Path, anchor: str, new_line: str) -> None:
    """Insert new_line right after the anchor line (whole-line match). Used to
    give each agent its OWN status line so parallel agents don't clobber each
    other. Falls back to appending at EOF if the anchor isn't found."""
    for _ in range(3):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")
            for i, ln in enumerate(lines):
                if ln.strip() == anchor.strip():
                    lines.insert(i + 1, new_line)
                    file_path.write_text("\n".join(lines), encoding="utf-8")
                    return
            lines.append(new_line)
            file_path.write_text("\n".join(lines), encoding="utf-8")
            return
        except OSError:
            time.sleep(0.1)


def _poll_agent_background(file_path: Path, agent_id: str, status_line: str,
                           command_name: str, log) -> None:
    """Daemon-thread poller: owns ONE per-agent status line, updates it from the
    registry, and swaps it to a done/error marker when finished. Multiple of
    these run concurrently for parallel agents — each keyed to its own line."""
    try:
        from geno_agents import read_status
    except ImportError:
        try:
            from geno_pear.agent import read_status  # legacy fallback
        except ImportError:
            return
    cur_line = status_line
    deadline = time.monotonic() + 1800  # 30 min cap
    while time.monotonic() < deadline:
        data = read_status(agent_id)
        if data:
            st = data.get("status", "running")
            msg = (data.get("message") or "")[:60]
            if st in ("done", "error"):
                mark = "done" if st == "done" else "error"
                final = f"// {mark} @ {time.strftime('%H:%M:%S')} · {command_name} //"
                _replace_line(file_path, cur_line, final)
                log(f"    {mark}: {command_name} ({agent_id})")
                return
            new_line = f"// heard · {command_name}: {msg} //"
            if new_line != cur_line:
                _replace_line(file_path, cur_line, new_line)
                cur_line = new_line
        time.sleep(2)
    # timed out
    _replace_line(file_path, cur_line, f"// error @ {time.strftime('%H:%M:%S')} · {command_name} timed out //")


def _run_with_feedback(
    file_path: Path,
    commit_line: str,
    commands: list[tuple[str, str]],
    commands_dir: Path,
    freeform: str,
    log=print,
) -> None:
    """Execute commands. Agent-launching commands are FIRE-AND-FORGET: each gets
    its own status line + a background poller, and this function returns promptly
    so the watcher can accept more //// commits (enabling parallel agents).
    Synchronous commands run inline and finish before returning."""
    ts = time.strftime("%H:%M:%S")
    # First, consume the draft lines + the commit line so the watcher's status
    # gate clears immediately and a new //// can be committed while agents run.
    text = file_path.read_text(encoding="utf-8", errors="replace")
    for name, _a in commands:
        text = re.sub(rf"^///{re.escape(name)}[ \t]*[^\n]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(rf"^{re.escape(commit_line.strip())}\s*$\n?", "", text, count=1, flags=re.MULTILINE)
    file_path.write_text(text, encoding="utf-8")

    if freeform.strip():
        log(f"  📝 freeform: {freeform[:60].strip()}")

    launched_agents = 0
    for name, args in commands:
        script = load_command(commands_dir, name)
        if script is None:
            log(f"  ⚠ ///{name} — not in {commands_dir}")
            continue
        log(f"  ▶ ///{name} {args}".rstrip())

        rc, out, agent_id = execute_command(script, args=args, cwd=str(file_path.parent))
        if out:
            for line in out.splitlines()[:3]:
                log(f"    {line}")

        if agent_id:
            # Fire-and-forget: append a dedicated status line for this agent and
            # spawn a background poller. Do NOT block — lets the next command /
            # the next //// commit proceed in parallel.
            status_line = f"// heard · {name}: launching… //"
            _append_status_line(file_path, "", status_line)  # append at EOF
            log(f"    agent {agent_id} launched (background poll)")
            threading.Thread(
                target=_poll_agent_background,
                args=(file_path, agent_id, status_line, name, log),
                daemon=True,
            ).start()
            launched_agents += 1
        else:
            # Synchronous command finished; note it briefly at EOF.
            mark = "done" if rc == 0 else "error"
            _append_status_line(file_path, "",
                                f"// {mark} @ {time.strftime('%H:%M:%S')} · {name} //")
            log(f"    {'✓' if rc == 0 else '✗'} {name}")

    if launched_agents:
        log(f"  {launched_agents} agent(s) running in background — watcher free for new commits")


def check_commit(file_path: str | Path, log=print) -> bool:
    """Check for //// or ////command in file. If found, process and return True."""
    p = Path(file_path)
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8", errors="replace")

    # NOTE: no status-marker gate anymore. _run_with_feedback consumes the ////
    # commit line synchronously before launching any (fire-and-forget) agents,
    # so there's no re-trigger window — and per-agent status lines are allowed to
    # persist while multiple agents run in parallel. Only an unconsumed //// on a
    # subsequent save triggers a new batch.
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
            # Nothing to do — remove the //// line and leave
            _replace_line(p, commit_line, "")
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
