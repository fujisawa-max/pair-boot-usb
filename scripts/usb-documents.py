#!/usr/bin/env python3
"""Prepare Japanese user guides and their local Markdown links for USB copies."""
from pathlib import Path
import os
import re
from urllib.parse import unquote, urlsplit

ENTRY_NAMES = {
    'docs/quick-start.md': 'QUICK-START.md',
    'docs/user-guide.md': 'DETAILS.md',
    'docs/no-gpu-test.md': 'NO-GPU-TEST.md',
    'docs/windows-fleet.md': 'WINDOWS-FLEET.md',
}
LINK = re.compile(r'(?<!!)\[([^\]\n]+)\]\(([^\s)]+)\)')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def regular(path, root):
    # Resolve ../ links inside the repository, but never follow a source symlink.
    path = Path(path.absolute())
    path = Path(os.path.normpath(path))
    if not path.is_relative_to(root) or path.suffix != '.md':
        raise ValueError('USB document link must name repository Markdown: ' + str(path))
    relative = path.relative_to(root)
    if not (relative.parts[0] == 'docs' and all(not part.startswith('.') for part in relative.parts)
            or relative.as_posix() in ('README.md', 'README.ja.md', 'THIRD-PARTY.md', 'TODO.md')):
        raise ValueError('USB document link must name a public user document: ' + str(path))
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink():
            raise ValueError('Refusing symlink USB document: ' + str(path))
    return path


def japanese_source(path, root):
    path = regular(path, root)
    candidate = path if path.name.endswith('.ja.md') else path.with_name(path.stem + '.ja.md')
    if candidate.exists() or candidate.is_symlink():
        path = regular(candidate, root)
    if not path.is_file():
        raise ValueError('Missing USB document: ' + str(path))
    return path


def document_plan(repository):
    root = Path(repository).absolute()
    pending = [root / name for name in ENTRY_NAMES]
    sources, names, documents = {}, {}, {}

    def register(path):
        source = japanese_source(path, root)
        if source not in sources:
            relative = source.relative_to(root).as_posix()
            canonical = relative.removesuffix('.ja.md') + '.md' if relative.endswith('.ja.md') else relative
            name = ENTRY_NAMES.get(canonical, Path(canonical).name)
            if name.casefold() in names and names[name.casefold()] != source:
                raise ValueError('USB document filenames collide: ' + name)
            sources[source] = name
            names[name.casefold()] = source
            pending.append(source)
        return source, sources[source]

    def local_target(source, target):
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or not parts.path:
            return None
        path = Path(unquote(parts.path))
        if path.is_absolute():
            raise ValueError('Absolute local USB document link: ' + target)
        return regular(source.parent / path, root), parts

    def rewrite(source, label, target):
        local = local_target(source, target)
        if local is None:
            return '[' + label + '](' + target + ')'
        path, parts = local
        selected, name = register(path)
        for old in (path.name, selected.name):
            if old in label:
                label = label.replace(old, name)
        suffix = ('?' + parts.query if parts.query else '') + ('#' + parts.fragment if parts.fragment else '')
        return '[' + label + '](' + name + suffix + ')'

    while pending:
        source, name = register(pending.pop(0))
        if name in documents:
            continue
        lines, fence, skip_blank = [], None, False
        for line in source.read_text(encoding='utf-8').splitlines(keepends=True):
            marker = FENCE.match(line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                lines.append(line)
                continue
            if fence is not None:
                lines.append(line)
                continue
            if skip_blank:
                skip_blank = False
                if not line.strip():
                    continue
            switch = LINK.fullmatch(line.strip())
            if switch and source.name.endswith('.ja.md'):
                local = local_target(source, switch.group(2))
                if local and local[0] == source.with_name(source.name.removesuffix('.ja.md') + '.md'):
                    skip_blank = True
                    continue
            lines.append(LINK.sub(lambda match: rewrite(source, *match.groups()), line))
        if fence is not None:
            raise ValueError('Unclosed Markdown fence: ' + str(source))
        documents[name] = ''.join(lines)
    return documents


def write_documents(destination, documents):
    destination = Path(destination).absolute()
    if not destination.is_dir() or any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError('Ordinary document staging directory required')
    existing = {path.name.casefold() for path in destination.iterdir()}
    for name in documents:
        if Path(name).name != name or name.casefold() in existing:
            raise ValueError('USB document output exists or is unsafe: ' + name)
        existing.add(name.casefold())
    for name, content in documents.items():
        with (destination / name).open('x', encoding='utf-8', newline='') as stream:
            stream.write(content)
