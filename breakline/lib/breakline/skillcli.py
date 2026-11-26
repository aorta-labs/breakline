"""Shared plumbing for the skill's scripts.

Contract for every script: JSON on stdout, human-readable text on stderr.
Exit codes: 0 ok, 2 parity failed, 3 configuration or usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

OK, PARITY_FAIL, CONFIG_ERROR = 0, 2, 3
SKILL_DIR = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = SKILL_DIR / "references" / "registry.json"


class ConfigError(Exception):
    """Bad flags, missing files or unusable configuration."""


class Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse would exit 2, which means "parity failed" here
        raise ConfigError(message)


def parser(doc: str | None) -> Parser:
    return Parser(description=(doc or "").strip().splitlines()[0] if doc else None,
                  epilog="stdout: JSON. stderr: text. exit 0 ok, 2 parity fail, 3 config error.")


def add_common(p: argparse.ArgumentParser, repo: bool = True) -> None:
    if repo:
        p.add_argument("repo", help="path to the repository to work on")
    p.add_argument("--registry", help=f"registry JSON (default: {DEFAULT_REGISTRY})")
    p.add_argument("--today", help="override today's date, YYYY-MM-DD")


def log(*parts) -> None:
    print(*parts, file=sys.stderr)


def emit(obj: dict) -> None:
    print(json.dumps(obj, indent=2, default=str))


def today(args) -> dt.date:
    try:
        return dt.date.fromisoformat(args.today) if getattr(args, "today", None) else dt.date.today()
    except ValueError:
        raise ConfigError(f"--today must be YYYY-MM-DD, got {args.today!r}") from None


def repo_path(args) -> Path:
    p = Path(args.repo).expanduser().resolve()
    if not p.is_dir():
        raise ConfigError(f"repo path is not a directory: {p}")
    return p


def registry_path(args) -> Path:
    return Path(args.registry).expanduser() if getattr(args, "registry", None) else DEFAULT_REGISTRY


def registry_meta(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {"registry_version": raw.get("registry_version"), "note": raw.get("note")}
    except (OSError, json.JSONDecodeError):
        return {}


def out_dir(args, repo: Path) -> Path:
    return Path(args.out).expanduser().resolve() if getattr(args, "out", None) else repo / ".breakline" / "out"


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from None


def parse_pairs(items: list[str] | None, flag: str) -> dict[str, str]:
    out = {}
    for item in items or []:
        old, sep, new = item.partition("=")
        if not sep or not old.strip() or not new.strip():
            raise ConfigError(f"{flag} expects OLD=NEW, got {item!r}")
        out[old.strip()] = new.strip()
    return out


def site_key(site: dict) -> tuple:
    f = site["finding"]
    return (f["path"], f["line"], f["change_id"], site.get("replacement"))


def _redact(text: str) -> str:
    """Never let an API key reach the output, even by accident."""
    for k, v in os.environ.items():
        if v and len(v) >= 8 and any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(v, "[redacted]")
    return text


def run(main) -> None:
    """Run a script's main(); turn every failure into JSON on stdout and exit 3."""
    try:
        if sys.version_info < (3, 10):
            raise ConfigError(f"Python 3.10 or newer is required, found {sys.version.split()[0]}")
        try:
            code = main() or OK
        except ImportError as e:
            if "yaml" in str(e).lower():
                raise ConfigError("PyYAML is missing: run `python3 -m pip install pyyaml`") from None
            raise
    except SystemExit as e:  # --help
        raise e
    except ConfigError as e:
        emit({"ok": False, "error": _redact(str(e))})
        log(f"error: {_redact(str(e))}")
        sys.exit(CONFIG_ERROR)
    except Exception as e:  # noqa: BLE001 - report as JSON; keep the traceback for debugging only
        msg = _redact(f"{type(e).__name__}: {e}")
        emit({"ok": False, "error": msg})
        log(f"error: {msg}")
        if os.environ.get("BREAKLINE_DEBUG"):
            log(_redact(traceback.format_exc()))
        sys.exit(CONFIG_ERROR)
    sys.exit(code)
