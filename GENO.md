# geno-pear — pair programming companion

A persistent file-monitoring companion that watches a code file for changes
and writes contextual feedback to a sidecar markdown file. The user codes in
their editor; geno-pear observes each save and updates a scratchpad visible
in the IDE sidebar.

Personas govern the kind of feedback: a reviewer catches bugs, a mentor
discusses design, a pair programmer thinks alongside you. Other skillsets
can inject custom personas (e.g., geno-career injects a tutor persona for
interview prep).

## Skills

| Skill | Slash command | Description |
|-------|---------------|-------------|
| `geno-pear` | `/gt-pear` | Start a file-monitoring companion session |

## Repo structure

| Path | Purpose |
|------|---------|
| `GENO.md` | Canonical agent instructions (this file) |
| `SKILL.md` | Symlink to `skills/geno-pear/SKILL.md` |
| `genotools.yaml` | Install manifest |
| `skills/geno-pear/SKILL.md` | Skill definition |
| `config/defaults/pear.yaml` | Default configuration |

## Data layout

Session data lives in `.geno/geno-pear/` — discovered by walking up from the
current directory until a `.geno/` directory is found.

```
.geno/geno-pear/
└── sessions/
    └── {YYYYMMDD-HHMM}/
        ├── session.yaml       # Session metadata
        ├── scratchpad.md      # Final scratchpad state (persisted copy)
        ├── session-log.json   # All AI channel interactions with timestamps
        └── history/           # Timestamped scratchpad snapshots
            └── {YYYYMMDD-HHMMSS}.md
```

## Core concepts

### File monitoring

Uses the Monitor tool with `persistent: true` and a platform-aware polling
loop (`stat -f %m` on macOS, `stat -c %Y` on Linux) at 1-second intervals.
Each file modification triggers a read-analyze-write cycle.

### AI channel

Bidirectional communication via comment markers in the watched file. The user
writes between markers to ask questions or issue commands; the companion reads
them, responds in the scratchpad, and clears the markers.

Markers auto-detect from file extension:
- C/C++/Java/JS/Go/Rust: `// START AI CHANNEL` / `// END AI CHANNEL`
- Python/Bash/Ruby: `# START AI CHANNEL` / `# END AI CHANNEL`
- SQL/Haskell: `-- START AI CHANNEL` / `-- END AI CHANNEL`

### Scratchpad

The scratchpad is a markdown file written adjacent to the watched file (or at
a custom path). It shows the **current state** of feedback — each save
overwrites the previous content. After each write, the scratchpad is also
copied to a `history/` directory with a timestamp filename, preserving every
version. Historical interactions are preserved in `session-log.json`.

Optimized for IDE sidebar viewing: ~60 char line width, no walls of text.

### Personas

Personas shape **what** the companion does, not just how it talks. Each
persona defines behavioral rules, analysis focus, and scratchpad format.

Built-in personas: `reviewer` (default), `mentor`, `pair`.

The `custom` persona reads instructions from the `--context` file, enabling
other skillsets to inject their own behavior.

## Conventions

### Prefix aliasing

The slash command `/gt-pear` maps to the `geno-pear` skill namespace.

### Skill creation

New skills (if added) must:
1. Create a directory under `skills/` named `geno-pear-{slug}/`
2. Add a `SKILL.md` with frontmatter
3. Register in the skills table above
