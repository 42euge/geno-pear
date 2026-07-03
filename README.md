# geno-pear

Pair programming companion for the [geno ecosystem](https://github.com/42euge/geno-tools).

Watches a code file for changes and writes contextual feedback to a sidecar
markdown file. Open the scratchpad in your IDE sidebar while you code.

## Install

```bash
geno-tools install pear
```

## Usage

```
/gt-pear <file-path> [--persona reviewer|mentor|pair|custom] [--scratchpad <path>] [--context <path>]
```

### Examples

```bash
# Review mode (default) — catches bugs and style issues
/gt-pear src/main.cpp

# Mentor mode — discusses design decisions and trade-offs
/gt-pear src/main.cpp --persona mentor

# Pair mode — thinks alongside you, conversational
/gt-pear src/main.cpp --persona pair

# Custom persona from another skillset
/gt-pear blank.cpp --persona custom --context tutor-rules.md
```

### AI channel

Communicate with the companion by writing between markers in your code file:

```cpp
// START AI CHANNEL
What's the time complexity of this approach?
// END AI CHANNEL
```

Save the file and check the scratchpad for the response.

**Commands:** `switch <persona>`, `status`, `done`

## Personas

| Persona | Behavior | Key rule |
|---------|----------|----------|
| `reviewer` | Direct code review with categorized findings | Always provides the fix |
| `mentor` | Strategic/architectural discussion | Asks questions, discusses trade-offs |
| `pair` | Collaborative, conversational | Mirrors your energy, thinks alongside you |
| `custom` | Reads rules from `--context` file | Enables other skillsets to inject behavior |

## License

MIT

## Library

geno-pear's watch mechanism is importable — any geno tool can reuse it:

```python
from geno_pear import watch
watch("/path/to/file", on_change=lambda p: print(f"{p} changed"))  # blocks; return False to stop
```

geno-vault's `vault watch` composes exactly this. Also exposed as a CLI:
`pear watch <file> --exec "<cmd>"`.
