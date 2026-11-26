"""Usage Scanner: find call sites affected by registry changes.

Deterministic. Python uses the stdlib AST; JS/TS and config files use a
string-literal tokenizer. Matching is on the exact model ID, so aliases,
undated IDs and provider-prefixed IDs (openai/gpt-4o...) are NOT found here.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from .registry import Change

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".env", ".ini"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".breakline", ".next", "out", ".claude"}
MODEL_KEYS = {"model", "model_id", "modelname", "model_name", "engine", "deployment"}

# JS/TS string literal: '...', "...", or `...` without ${}
JS_STRING = re.compile(r"""(['"`])((?:\\.|(?!\1).)*?)\1""")
# YAML-style `key: value` with an unquoted value, optionally in a list and followed by a comment
YAML_PLAIN = re.compile(r"^\s*(?:-\s+)?[\w.\-]+\s*:\s*(?P<v>[^\s#'\"][^\s#]*)\s*(?:#.*)?$")


@dataclass
class Finding:
    change_id: str
    symbol: str
    path: str
    line: int
    col: int           # 0-based column of the literal's first character (inside quotes)
    end_col: int
    context: str       # "model_kwarg", "assignment", "env_default", "string_literal", "config", "pattern"
    confidence: float
    snippet: str

    def to_dict(self) -> dict:
        return asdict(self)


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file() and (p.suffix in CODE_EXT | CONFIG_EXT or p.name.startswith(".env")):
            yield p


def _python_findings(rel: str, text: str, models: dict[str, Change]) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines = text.splitlines()
    ctx: dict[int, tuple[str, float]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and kw.arg.lower() in MODEL_KEYS and isinstance(kw.value, ast.Constant):
                    ctx[id(kw.value)] = ("model_kwarg", 0.99)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if isinstance(node.value, ast.Constant) and any("model" in n.lower() for n in names):
                ctx[id(node.value)] = ("assignment", 0.95)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {"get", "getenv"}:
            # os.getenv("OPENAI_MODEL", "gpt-4o-...") -> default value
            for a in node.args[1:]:
                if isinstance(a, ast.Constant):
                    ctx[id(a)] = ("env_default", 0.9)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in models:
            ch = models[node.value]
            kind, conf = ctx.get(id(node), ("string_literal", 0.8))
            line = lines[node.lineno - 1]
            # ast offsets are UTF-8 bytes; convert to a character index before searching
            start = len(line.encode("utf-8")[: node.col_offset].decode("utf-8", errors="ignore"))
            col = line.find(node.value, start)
            if col < 0:
                col = line.find(node.value)
            if col < 0:
                continue  # value built from escapes or implicit concatenation: the tokenizer cannot place it
            out.append(Finding(ch.id, ch.symbol, rel, node.lineno, col, col + len(node.value), kind, conf, line.strip()))
    return out


def _literal_findings(rel: str, text: str, models: dict[str, Change], context: str) -> list[Finding]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("//", "#")):
            continue
        quoted = False
        for m in JS_STRING.finditer(line):
            quoted = True
            val = m.group(2)
            if val in models:
                ch = models[val]
                before = line[: m.start()].lower()
                conf = 0.97 if re.search(r"model\w*[\"']?\s*[:=]\s*$", before) else 0.8
                out.append(Finding(ch.id, ch.symbol, rel, i, m.start(2), m.end(2), context if conf < 0.9 else "model_kwarg", conf, line.strip()))
        if context == "config" and not quoted:
            val, col = None, 0
            if "=" in line:                         # .env style: OPENAI_MODEL=gpt-4o-2024-08-06
                key, _, rest = line.partition("=")
                val = rest.strip().split("#")[0].strip()
                col = line.index(rest.strip(), len(key)) if val else 0
            else:                                   # YAML plain scalar: model: gpt-4o-2024-08-06
                ym = YAML_PLAIN.match(line)
                if ym:
                    val, col = ym.group("v"), ym.start("v")
            if val and val in models:
                ch = models[val]
                out.append(Finding(ch.id, ch.symbol, rel, i, col, col + len(val), "config", 0.95, line.strip()))
    return out


def _pattern_findings(rel: str, text: str, pattern_changes: list[Change]) -> list[Finding]:
    out = []
    for ch in pattern_changes:
        for pat in ch.detect_patterns:
            rx = re.compile(pat)
            for i, line in enumerate(text.splitlines(), 1):
                m = rx.search(line)
                if m:
                    out.append(Finding(ch.id, ch.symbol, rel, i, m.start(), m.end(), "pattern", 0.7, line.strip()))
    return out


def scan(root: Path | str, changes: list[Change]) -> list[Finding]:
    root = Path(root)
    models = {c.symbol: c for c in changes if c.kind == "model_retirement"}
    pattern_changes = [c for c in changes if c.detect_patterns]
    findings: list[Finding] = []
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if p.suffix == ".py":
            findings += _python_findings(rel, text, models)
        elif p.suffix in CODE_EXT:
            findings += _literal_findings(rel, text, models, "string_literal")
        else:
            findings += _literal_findings(rel, text, models, "config")
        if p.suffix in CODE_EXT:
            findings += _pattern_findings(rel, text, pattern_changes)
    seen, uniq = set(), []
    for f in findings:
        key = (f.path, f.line, f.col, f.change_id)
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq
