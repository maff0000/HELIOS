"""Source scanning shared by the structural guard tests.

The guards enforce boundaries against *code and data* — identifiers, string
literals, configuration keys, fixture values — and deliberately not against
prose. A docstring, a comment or a document must be able to NAME a boundary in
order to explain it; that is the opposite of crossing it. Scanning prose would
force the codebase to be unable to describe its own rules.

Docstrings and comments are therefore excluded structurally (via the AST, and
because comments never reach it), not by an ad-hoc allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of the string constants that are docstrings."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(id(value))
    return found


def code_tokens(path: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(kind, text)`` for every identifier and non-docstring string.

    Comments are absent from the AST, so they are excluded automatically.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield ("identifier", node.id)
        elif isinstance(node, ast.Attribute):
            yield ("identifier", node.attr)
        elif isinstance(node, ast.arg):
            yield ("identifier", node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield ("identifier", node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            yield ("identifier", node.arg)
        elif isinstance(node, ast.alias):
            yield ("identifier", node.asname or node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield ("string", node.value)


def python_files(root: Path, *, exclude: frozenset[Path] = frozenset()) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and path.resolve() not in exclude
    )


def data_files(root: Path, *, exclude: frozenset[Path] = frozenset()) -> list[Path]:
    suffixes = {".json", ".yaml", ".yml", ".toml", ".txt"}
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in suffixes
        and path.resolve() not in exclude
    )


def strip_comment_lines(text: str, marker: str = "#") -> str:
    """Drop whole-line comments so a config/data file's prose is excluded."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(marker):
            continue
        kept.append(line)
    return "\n".join(kept)
