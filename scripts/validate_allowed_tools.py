#!/usr/bin/env python3
"""Validate that all SKILL.md and agent .md files in personal-os declare allowed-tools."""
import sys, pathlib, re

PERSONAL_OS = pathlib.Path(__file__).parent.parent.resolve()
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def check_file(path: pathlib.Path) -> tuple[bool, str]:
    content = path.read_text()
    m = FRONTMATTER.match(content)
    if not m:
        return False, "no frontmatter found"
    fm_text = m.group(1)
    if not re.search(r"^allowed-tools:\s*$", fm_text, re.MULTILINE):
        return False, "missing allowed-tools field"
    return True, "ok"


def main():
    errors = []
    checked = 0

    for plugin_dir in sorted(PERSONAL_OS.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
            continue
        for pattern in [
            f"{plugin_dir.name}/skills/*/SKILL.md",
            f"{plugin_dir.name}/agents/*.md",
        ]:
            for f in sorted(PERSONAL_OS.glob(pattern)):
                if "_archive" in str(f):
                    continue
                checked += 1
                ok, msg = check_file(f)
                if not ok:
                    rel = f.relative_to(PERSONAL_OS)
                    errors.append(f"  {rel}: {msg}")

    print(
        f"Checked {checked} files "
        f"({checked - len(errors)} OK, {len(errors)} MISSING allowed-tools)"
    )

    if errors:
        print("\nFiles missing allowed-tools:")
        for e in errors:
            print(e)
        return 1

    print("All files have allowed-tools frontmatter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
