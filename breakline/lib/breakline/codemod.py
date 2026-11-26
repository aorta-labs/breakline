"""Codemod Engine: deterministic rewrites of affected call sites.

Rule-based. A patch is kept only if the file still parses and the text at the
site is still exactly the old symbol. Otherwise the site is marked needs_human.
Files are read and written with newline="" so CRLF files keep their line endings.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .policy import Policy
from .registry import Change
from .scanner import Finding

# A replacement is spliced into source code, so it may only look like a model ID.
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}")


@dataclass
class SiteResult:
    finding: Finding
    action: str            # allow | ask | deny (from policy)
    status: str            # patched | skipped_deny | needs_human | no_replacement
    replacement: str | None = None
    reason: str = ""
    applied: bool = False  # True once the patch has been written to disk


@dataclass
class PatchSet:
    sites: list[SiteResult] = field(default_factory=list)
    new_text: dict[str, str] = field(default_factory=dict)   # path -> patched content
    diff: str = ""

    @property
    def patched(self) -> list[SiteResult]:
        return [s for s in self.sites if s.status == "patched"]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _parses(path: str, text: str) -> bool:
    try:
        if path.endswith(".py"):
            ast.parse(text)
        elif path.endswith(".json"):
            json.loads(text)
    except (SyntaxError, ValueError):
        return False
    return True


def _inside(root: Path, rel: str) -> bool:
    try:
        (root / rel).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def plan(root: Path | str, findings: list[Finding], changes: list[Change], policy: Policy) -> PatchSet:
    root = Path(root)
    by_id = {c.id: c for c in changes}
    ps = PatchSet()
    per_file: dict[str, list[SiteResult]] = defaultdict(list)

    for f in findings:
        ch = by_id[f.change_id]
        action = policy.decide(f.path, ch.provider, ch.id)
        if action == "deny":
            ps.sites.append(SiteResult(f, action, "skipped_deny", reason="policy: deny"))
            continue
        if ch.kind != "model_retirement":
            ps.sites.append(SiteResult(f, action, "needs_human", reason=f"{ch.kind}: no deterministic codemod"))
            continue
        repl = policy.replacements.get(ch.symbol) or ch.default_replacement
        if not repl:
            ps.sites.append(SiteResult(f, action, "no_replacement", reason="choose a replacement (policy.yaml or --replace)"))
            continue
        if not SAFE_ID.fullmatch(repl):
            ps.sites.append(SiteResult(f, action, "needs_human", repl, "replacement is not a plain model id"))
            continue
        if not _inside(root, f.path):
            ps.sites.append(SiteResult(f, action, "needs_human", repl, "file resolves outside the repo"))
            continue
        sr = SiteResult(f, action, "patched", replacement=repl)
        ps.sites.append(sr)
        per_file[f.path].append(sr)

    diffs = []
    for rel, sites in per_file.items():
        original = _read(root / rel)
        lines = original.splitlines(keepends=True)
        # apply right-to-left within a line so columns stay valid
        for sr in sorted(sites, key=lambda s: (s.finding.line, -s.finding.col)):
            f = sr.finding
            line = lines[f.line - 1] if f.line <= len(lines) else ""
            if line[f.col:f.end_col] != f.symbol:
                sr.status, sr.reason = "needs_human", "source moved since scan"
                continue
            lines[f.line - 1] = line[: f.col] + sr.replacement + line[f.end_col:]
        new = "".join(lines)
        if not _parses(rel, new):
            for sr in sites:
                if sr.status == "patched":
                    sr.status, sr.reason = "needs_human", "patched file failed to parse"
            continue
        if new == original:
            continue
        ps.new_text[rel] = new
        diffs.append("".join(difflib.unified_diff(
            original.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}")))
    ps.diff = "".join(diffs)
    return ps


def apply(root: Path | str, ps: PatchSet) -> None:
    root = Path(root)
    for rel, text in ps.new_text.items():
        with open(root / rel, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    for s in ps.patched:
        s.applied = True
