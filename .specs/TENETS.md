# Tenets

1. **Observe, don't interrupt.** All feedback goes to the scratchpad, never
   inline in the user's code (except clearing AI channel markers after reading).

2. **Current state, not history.** The scratchpad shows feedback about the
   code *right now*. Previous feedback is preserved only in qa-log.json.

3. **Persona shapes behavior, not just tone.** A reviewer gives fixes; a tutor
   withholds them. The persona changes *what* the companion does, not just how
   it talks.

4. **Portable primitives.** Use `stat` polling (available everywhere), not
   `fswatch`/`inotifywait` (not always installed). AI channel markers use
   standard comment syntax per language.

5. **Composable.** The `custom` persona lets other skillsets inject their own
   behavior. geno-pear is infrastructure, not a monolith.
