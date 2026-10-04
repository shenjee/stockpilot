"""Check local Markdown file targets without network access.

Supports inline links/images and reference definitions, including angle-bracket
destinations and optional titles. Ignores fenced/inline code, external schemes,
protocol-relative URLs, and same-page anchors. For file#anchor links only the
file is checked; heading slug validation and HTML links are outside this check.
"""

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


DESTINATION = r'<([^>\n]+)>|([^\s<>]+?)'
INLINE = re.compile(r'!?\[[^\]\n]*\]\(\s*(?:' + DESTINATION + r')(?:\s+["\'][^\n]*?["\'])?\s*\)')
REFERENCE = re.compile(r'^\s{0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))')


def destinations(source):
    """Yield (line number, destination), excluding Markdown code examples."""
    fence = None
    for number, line in enumerate(source.splitlines(), 1):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        if line.startswith(('    ', '\t')):
            continue
        line = re.sub(r'(`+).*?\1', '', line)
        reference = REFERENCE.match(line)
        matches = [reference] if reference else INLINE.finditer(line)
        for match in matches:
            yield number, match[1] or match[2]


def broken_links(path, root):
    failures = []
    for number, destination in destinations(path.read_text(encoding='utf-8')):
        url = urlsplit(destination)
        if url.scheme or url.netloc or not url.path:
            continue
        target = unquote(url.path)
        resolved = root / target.lstrip('/') if target.startswith('/') else path.parent / target
        if not resolved.exists():
            failures.append(f'{path.relative_to(root)}:{number}: {destination}')
    return failures


def main():
    root = Path(__file__).resolve().parents[1]
    # Include new docs before staging, but exclude ignored generated dependencies.
    names = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', '*.md'],
        cwd=root,
    ).decode().split('\0')
    paths = [root / name for name in names if name and not name.startswith('skills/')]
    failures = [failure for path in paths for failure in broken_links(path, root)]
    for failure in failures:
        print(failure)
    print(f'Checked {len(paths)} Markdown files; {len(failures)} missing local targets (skills/ excluded).')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
