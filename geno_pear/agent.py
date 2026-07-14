"""geno-agent — agent registry for geno-pear ///command sessions.

Tracks long-running agents (e.g. Claude Code sessions launched by ///commands)
with a file-based registry in ~/.geno/agents/. Each agent gets a JSON status
file and a log file. The watcher polls the JSON to write live status back to
the markdown file, and only writes `// done //` when the agent signals completion.

CLI:
  geno-agent run --id ID --source FILE [--log FILE] [--output FILE] -- <cmd...>
  geno-agent done ID [--message MSG]     signal completion
  geno-agent error ID [--message MSG]    signal failure
  geno-agent status [ID]                 print JSON
  geno-agent ls                          list agents (with status)
  geno-agent wait ID [--timeout S]       block until done/error
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

AGENTS_DIR = Path.home() / ".geno" / "agents"

# Strip ANSI/VT escape sequences when extracting status text from the PTY stream.
# CSI: ESC [ <params 0x30-3f> <intermediates 0x20-2f> <final 0x40-7e>
# OSC: ESC ] ... (BEL | ST)   DCS/other: ESC P/_/^/X ... ST
# Also: charset selects, single ESC-<byte>, and bare control chars.
_ANSI = re.compile(
    rb"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"     # CSI
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"            # OSC ... BEL/ST
    rb"|\x1b[P_^X][^\x1b]*\x1b\\"                     # DCS/APC/PM/SOS ... ST
    rb"|\x1b[()][AB0-2]"                              # charset select
    rb"|\x1b[=>NODME78]"                              # misc single-char ESC
    rb"|\x1b."                                        # any other ESC-<byte>
    rb"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"             # bare control chars
)

# Sentinel prefix printed by commands that launch tracked agents
AGENT_ID_PREFIX = "GENO_AGENT_ID="


def _agents_dir() -> Path:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    return AGENTS_DIR


def _json_path(agent_id: str) -> Path:
    return _agents_dir() / f"{agent_id}.json"


def _log_path(agent_id: str) -> Path:
    return _agents_dir() / f"{agent_id}.log"


def write_status(agent_id: str, status: str, message: str = "",
                 source_file: str = "", output_file: str = "") -> dict:
    p = _json_path(agent_id)
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except Exception:
            pass
    data.update({
        "id": agent_id,
        "status": status,
        "message": message,
        "updated": time.strftime("%H:%M:%S"),
    })
    if source_file:
        data["source_file"] = source_file
    if output_file:
        data["output_file"] = output_file
    if "started" not in data:
        data["started"] = time.strftime("%H:%M:%S")
    p.write_text(json.dumps(data, indent=2))
    return data


def read_status(agent_id: str) -> dict | None:
    p = _json_path(agent_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def list_agents() -> list[dict]:
    d = _agents_dir()
    agents = []
    for p in sorted(d.glob("*.json")):
        try:
            agents.append(json.loads(p.read_text()))
        except Exception:
            pass
    return agents


def wait_for_agent(agent_id: str, timeout: float = 300.0, poll: float = 2.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = read_status(agent_id)
        if data and data.get("status") in ("done", "error"):
            return data
        time.sleep(poll)
    return None


def _pretrust_claude_dir(cwd: str) -> None:
    """Pre-accept Claude Code's folder-trust dialog for cwd so an interactive
    session launched by geno-agent doesn't block on 'Do you trust this folder?'
    (nobody is at the keyboard to press Enter). Idempotent, best-effort."""
    cfg = Path.home() / ".claude.json"
    try:
        data = json.loads(cfg.read_text()) if cfg.exists() else {}
    except Exception:
        return
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(cwd, {})
    changed = False
    for key, val in (("hasTrustDialogAccepted", True),
                     ("hasCompletedProjectOnboarding", True)):
        if entry.get(key) != val:
            entry[key] = val
            changed = True
    if changed:
        try:
            cfg.write_text(json.dumps(data, indent=2))
        except Exception:
            pass


def _close_own_iterm_tab() -> None:
    """Close the iTerm session this process is running in, using ITERM_SESSION_ID.

    iTerm sets ITERM_SESSION_ID like 'w0t1p0:UUID'. We match the session whose
    id ends with that UUID and close it. No-op if not running under iTerm.
    """
    import os
    sid = os.environ.get("ITERM_SESSION_ID", "")
    if not sid:
        return
    # ITERM_SESSION_ID format: "w0t1p0:<UUID>" — the UUID is the session's unique id
    uuid = sid.split(":")[-1]
    script = f'''
    tell application "iTerm2"
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            if (id of s) contains "{uuid}" then
              close t
              return
            end if
          end repeat
        end repeat
      end repeat
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:
        pass


def _last_meaningful_line(buf: bytes) -> str:
    """Extract the last non-empty human-readable line from a PTY byte buffer."""
    text = _ANSI.sub(b"", buf).decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
    for ln in reversed(lines):
        if len(ln) <= 2:
            continue
        # Skip box-drawing / prompt-decoration-only lines
        if all(c in "─│╭╮╰╯━┃┏┓┗┛ >·•*✳✽✻✢·" for c in ln):
            continue
        # Require the line to be mostly letters (rejects leftover escape cruft
        # like "[>0q[?2026$p" which is symbols/digits only)
        letters = sum(c.isalpha() or c.isspace() for c in ln)
        if letters < max(3, len(ln) * 0.5):
            continue
        return ln[:80]
    return ""


def run_agent_pty(agent_id: str, cmd: list[str], source_file: str = "",
                  output_file: str = "", log_file: str = "",
                  close_on_done: bool = False) -> int:
    """Run cmd in a PTY so it keeps FULL interactive terminal functionality
    (Claude Code renders its TUI, accepts keystrokes), while geno-agent tees
    the output stream to extract live status for the registry.

    This is the interactive counterpart to run_agent(): the user sees and can
    drive Claude Code normally in the iTerm tab; geno-agent sits transparently
    in the middle, forwarding stdin<->pty and mirroring pty->stdout, and every
    2s writes the last meaningful output line to ~/.geno/agents/<id>.json.
    """
    import pty
    import select
    import termios
    import tty
    import struct
    import fcntl
    import signal

    log_path = Path(log_file) if log_file else _log_path(agent_id)
    write_status(agent_id, "running", "starting…",
                 source_file=source_file, output_file=output_file)

    # Pre-accept Claude's folder-trust dialog for our cwd so the interactive
    # session doesn't hang waiting for a keypress nobody will give.
    _pretrust_claude_dir(os.getcwd())

    # Fork a child attached to a new PTY
    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: exec the command (inherits the slave PTY as its controlling tty)
        try:
            os.execvp(cmd[0], cmd)
        except Exception as e:
            sys.stderr.write(f"exec failed: {e}\n")
            os._exit(127)

    # Parent: relay between our stdio and the child PTY
    ring = bytearray()          # rolling buffer of recent output for status
    logf = open(log_path, "ab", buffering=0)

    # Put our own stdin in raw mode so keystrokes pass straight through
    stdin_fd = sys.stdin.fileno()
    old_termios = None
    try:
        old_termios = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
    except Exception:
        pass

    # Propagate terminal size to the PTY, and on SIGWINCH
    def _set_winsize():
        try:
            sz = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, sz)
        except Exception:
            pass
    _set_winsize()
    try:
        signal.signal(signal.SIGWINCH, lambda *_: _set_winsize())
    except Exception:
        pass

    # Status ticker: every 2s parse the ring buffer for the latest line
    stop = threading.Event()

    def _ticker():
        while not stop.wait(2):
            msg = _last_meaningful_line(bytes(ring))
            if msg:
                # don't clobber a self-signalled done/error
                cur = read_status(agent_id)
                if cur and cur.get("status") in ("done", "error"):
                    return
                write_status(agent_id, "running", msg,
                             source_file=source_file, output_file=output_file)
    threading.Thread(target=_ticker, daemon=True).start()

    try:
        while True:
            try:
                rlist, _, _ = select.select([master_fd, stdin_fd], [], [], 0.1)
            except (OSError, ValueError):
                break
            if master_fd in rlist:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)   # mirror to real screen
                logf.write(data)                       # tee to log
                ring.extend(data)
                if len(ring) > 8192:
                    del ring[:-8192]
            if stdin_fd in rlist:
                try:
                    inp = os.read(stdin_fd, 4096)
                except OSError:
                    inp = b""
                if inp:
                    os.write(master_fd, inp)           # forward keystrokes
    finally:
        stop.set()
        if old_termios is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
            except Exception:
                pass
        logf.close()

    _, status = os.waitpid(pid, 0)
    rc = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1

    data = read_status(agent_id)
    if data and data.get("status") == "done":
        pass  # agent signalled itself
    elif rc == 0:
        write_status(agent_id, "done", "completed (exit 0)",
                     source_file=source_file, output_file=output_file)
    else:
        write_status(agent_id, "error", f"exit {rc}",
                     source_file=source_file, output_file=output_file)

    if close_on_done:
        time.sleep(3)
        _close_own_iterm_tab()
    return rc


def run_agent(agent_id: str, cmd: list[str], source_file: str = "",
              output_file: str = "", log_file: str = "",
              close_on_done: bool = False) -> int:
    """Launch cmd as a subprocess, streaming output to log_file.
    Updates the agent JSON with status and last log line every 2s.
    If close_on_done, tears down the iTerm tab after the agent finishes.
    Returns exit code."""
    log_path = Path(log_file) if log_file else _log_path(agent_id)
    write_status(agent_id, "running", "starting…",
                 source_file=source_file, output_file=output_file)

    lines_seen: list[str] = []

    def _tail_output(proc):
        with open(log_path, "a") as lf:
            for raw in proc.stdout:
                line = raw.rstrip()
                lf.write(line + "\n")
                lf.flush()
                lines_seen.append(line)

    # If cmd is a single string element it's a shell command; run via bash so
    # aliases, $(...) expansions, and PATH from ~/.zshrc/.bashrc all work.
    if len(cmd) == 1:
        shell_cmd = cmd[0]
        proc = subprocess.Popen(
            ["bash", "-i", "-c", shell_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    else:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    tail_thread = threading.Thread(target=_tail_output, args=(proc,), daemon=True)
    tail_thread.start()

    # Periodic status updates
    def _ticker():
        while proc.poll() is None:
            time.sleep(2)
            msg = lines_seen[-1][:80] if lines_seen else "running…"
            write_status(agent_id, "running", msg,
                         source_file=source_file, output_file=output_file)

    ticker = threading.Thread(target=_ticker, daemon=True)
    ticker.start()

    proc.wait()
    tail_thread.join(timeout=2)
    ticker.join(timeout=0.5)

    rc = proc.returncode
    if rc == 0:
        # Check if agent called `geno-agent done` explicitly; if so don't overwrite
        data = read_status(agent_id)
        if data and data.get("status") == "done":
            pass  # agent signalled itself
        else:
            write_status(agent_id, "done", "completed (exit 0)",
                         source_file=source_file, output_file=output_file)
    else:
        write_status(agent_id, "error", f"exit {rc}",
                     source_file=source_file, output_file=output_file)

    # Tear down the iTerm tab once the agent is done, so tabs don't accumulate.
    if close_on_done:
        # brief pause so the watcher's final poll sees the done/error status
        time.sleep(3)
        _close_own_iterm_tab()
    return rc


def main(argv: list[str] | None = None) -> int:
    import argparse
    argv = list(sys.argv[1:]) if argv is None else list(argv)

    p = argparse.ArgumentParser(prog="geno-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    # run
    p_run = sub.add_parser("run", help="launch a tracked agent subprocess")
    p_run.add_argument("--id", required=True, dest="agent_id")
    p_run.add_argument("--source", default="", help="source markdown file")
    p_run.add_argument("--output", default="", help="output file the agent edits")
    p_run.add_argument("--log", default="", help="log file path")
    p_run.add_argument("--close-on-done", action="store_true",
                       help="close the iTerm tab when the agent finishes")
    p_run.add_argument("--interactive", action="store_true",
                       help="run in a PTY so the command keeps full interactive "
                            "terminal functionality (TUI, keystrokes) while geno-agent "
                            "tees output for status tracking")
    p_run.add_argument("rest", nargs=argparse.REMAINDER, help="command to run (after --)")

    # done / error
    p_done = sub.add_parser("done", help="signal agent completed successfully")
    p_done.add_argument("agent_id")
    p_done.add_argument("--message", default="done")

    p_err = sub.add_parser("error", help="signal agent failed")
    p_err.add_argument("agent_id")
    p_err.add_argument("--message", default="error")

    # status
    p_status = sub.add_parser("status", help="print agent status JSON")
    p_status.add_argument("agent_id", nargs="?", default=None)

    # ls
    sub.add_parser("ls", help="list all agents")

    # wait
    p_wait = sub.add_parser("wait", help="block until agent finishes")
    p_wait.add_argument("agent_id")
    p_wait.add_argument("--timeout", type=float, default=300.0)

    args = p.parse_args(argv)

    if args.cmd == "run":
        cmd = args.rest
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            raise SystemExit("geno-agent run: no command given after --")
        runner = run_agent_pty if args.interactive else run_agent
        rc = runner(
            args.agent_id, cmd,
            source_file=args.source,
            output_file=args.output,
            log_file=args.log,
            close_on_done=args.close_on_done,
        )
        return rc

    elif args.cmd == "done":
        data = write_status(args.agent_id, "done", args.message)
        print(f"agent {args.agent_id}: done")
        return 0

    elif args.cmd == "error":
        data = write_status(args.agent_id, "error", args.message)
        print(f"agent {args.agent_id}: error")
        return 1

    elif args.cmd == "status":
        if args.agent_id:
            data = read_status(args.agent_id)
            print(json.dumps(data or {"error": "not found"}, indent=2))
        else:
            for a in list_agents():
                print(json.dumps(a, indent=2))
        return 0

    elif args.cmd == "ls":
        agents = list_agents()
        if not agents:
            print("no agents")
            return 0
        for a in agents:
            status = a.get("status", "?")
            msg = a.get("message", "")[:50]
            print(f"  {a['id']:<40} {status:<8} {msg}")
        return 0

    elif args.cmd == "wait":
        data = wait_for_agent(args.agent_id, timeout=args.timeout)
        if data:
            print(f"agent {args.agent_id}: {data['status']} — {data.get('message','')}")
            return 0 if data["status"] == "done" else 1
        print(f"timeout waiting for {args.agent_id}")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
