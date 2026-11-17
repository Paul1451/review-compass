"""Import index: which files at a revision import a given file (Python and JS/TS).

Raw import specifiers are cached per blob sha, so repeated calls across many
pull requests only parse files whose content actually changed.
"""

import ast
import json
import posixpath
import re
import warnings
from collections.abc import Sequence
from pathlib import Path

from review_compass.gitrepo import GitRepo

SUPPORTED_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_JS_EXTENSIONS = tuple(e for e in SUPPORTED_EXTENSIONS if e != ".py")
_MAX_JS_BYTES = 1024 * 1024

_JS_PATTERNS = (
    re.compile(r"""\bfrom\s*['"]([^'"\n]+)['"]"""),  # import … from / export … from
    re.compile(r"""\bimport\s*['"]([^'"\n]+)['"]"""),  # side-effect import
    re.compile(r"""\brequire\s*\(\s*['"]([^'"\n]+)['"]\s*\)"""),
)


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def _cache_key(path: str, sha: str) -> str:
    # The same blob can sit under a .py and a .js name; the parsers differ.
    return f"{'py' if _is_python(path) else 'js'}:{sha}"


def _python_specifiers(text: str) -> list[str]:
    """Dotted module names; relative ones keep their leading dots."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            found.add(prefix)  # `from . import x` also depends on the package itself
            joiner = "" if prefix.endswith(".") else "."
            found.update(f"{prefix}{joiner}{a.name}" for a in node.names if a.name != "*")
    found.discard("")
    return sorted(found)


def _js_specifiers(text: str) -> list[str]:
    found: set[str] = set()
    for pattern in _JS_PATTERNS:
        for m in pattern.finditer(text):
            spec = m.group(1)
            if spec.startswith(("./", "../")):
                found.add(spec)
    return sorted(found)


def _parse(path: str, data: bytes) -> list[str]:
    if _is_python(path):
        return _python_specifiers(data.decode("utf-8", errors="replace"))
    if len(data) > _MAX_JS_BYTES:
        return []
    return _js_specifiers(data.decode("utf-8", errors="replace"))


def _python_module_of(path: str) -> str:
    """Slash-joined module path of a .py file ('' for a root-level __init__)."""
    if path.endswith("/__init__.py"):
        return path[: -len("/__init__.py")]
    if path == "__init__.py":
        return ""
    return path[: -len(".py")]


def _resolve_relative(spec: str, importer: str) -> str | None:
    level = len(spec) - len(spec.lstrip("."))
    rest = spec[level:]
    package = importer.split("/")[:-1]
    if level - 1 > len(package):
        return None
    base = package[: len(package) - (level - 1)]
    parts = base + (rest.split(".") if rest else [])
    return "/".join(parts) if parts else None


class ImportIndex:
    def __init__(self, repo: GitRepo, cache_path: Path | None = None) -> None:
        self._repo = repo
        self._cache_path = cache_path
        self._cache = self._load()

    def _load(self) -> dict[str, list[str]]:
        if self._cache_path is None:
            return {}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            k: v
            for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, list) and all(isinstance(s, str) for s in v)
        }

    def save(self) -> None:
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache), encoding="utf-8")

    def dependents(self, rev: str, paths: Sequence[str]) -> dict[str, set[str]] | None:
        targets = [p for p in paths if p.endswith(SUPPORTED_EXTENSIONS)]
        if not targets:
            return None
        tree = {
            p: sha for p, sha in self._repo.ls_tree(rev).items() if p.endswith(SUPPORTED_EXTENSIONS)
        }
        specs = self._specifiers(tree)
        result: dict[str, set[str]] = {t: set() for t in targets}

        # Python: absolute specifiers match by module suffix, relative ones exactly.
        exact: dict[str, set[str]] = {}
        by_suffix: dict[str, set[str]] = {}
        for target in result:
            if not _is_python(target):
                continue
            module = _python_module_of(target)
            if not module:
                continue
            exact.setdefault(module, set()).add(target)
            segments = module.split("/")
            for i in range(len(segments)):
                by_suffix.setdefault("/".join(segments[i:]), set()).add(target)

        for importer, importer_specs in specs.items():
            if _is_python(importer):
                for spec in importer_specs:
                    if spec.startswith("."):
                        resolved_mod = _resolve_relative(spec, importer)
                        hits = exact.get(resolved_mod, set()) if resolved_mod else set()
                    else:
                        hits = by_suffix.get(spec.replace(".", "/"), set())
                    for target in hits:
                        if target != importer:
                            result[target].add(importer)
            else:
                base = posixpath.dirname(importer)
                for spec in importer_specs:
                    joined = posixpath.normpath(posixpath.join(base, spec))
                    resolved = self._resolve_js(joined, tree)
                    if resolved is not None and resolved != importer and resolved in result:
                        result[resolved].add(importer)
        return result

    def _specifiers(self, tree: dict[str, str]) -> dict[str, list[str]]:
        keyed = {path: _cache_key(path, sha) for path, sha in tree.items()}
        missing = {key: path for path, key in keyed.items() if key not in self._cache}
        if missing:
            blobs = self._repo.read_blobs(tree[path] for path in missing.values())
            for key, path in missing.items():
                data = blobs.get(tree[path])
                if data is not None:  # a missing blob must not poison the persistent cache
                    self._cache[key] = _parse(path, data)
        return {path: self._cache.get(key, []) for path, key in keyed.items()}

    @staticmethod
    def _resolve_js(base: str, tree: dict[str, str]) -> str | None:
        candidates = [base]
        candidates += [base + ext for ext in _JS_EXTENSIONS]
        candidates += [f"{base}/index{ext}" for ext in _JS_EXTENSIONS]
        return next((c for c in candidates if c in tree), None)
