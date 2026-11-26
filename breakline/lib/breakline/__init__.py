"""Breakline, vendored into the breakline Agent Skill.

Stdlib + PyYAML only. Changes from the proof-of-concept package this was copied from:
- registry data lives in the skill's references/registry.json
- every file path is a parameter (policy, fixtures, prices, router, simulator profiles)
- CRLF files keep their line endings; non-ASCII lines are located by character, not byte
- YAML values written without quotes are found
- parity can no longer pass silently: lost numeric fields and failed regexes fail,
  and missing prices, no fixtures or unchecked expectations are warnings
- a repo's router.yaml cannot redirect keys or fixtures without an explicit flag (see scripts/parity.py)
- a PR report is never "PASS" when parity did not run, and simulated runs are labelled
"""

__version__ = "0.2.0"
