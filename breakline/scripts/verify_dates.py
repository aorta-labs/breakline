#!/usr/bin/env python3
"""List registry records whose dates are still marked unverified.

Read-only. With --repo, also says which of them match code in that repo, so
you know which dates to confirm against the provider page before quoting them.
Usage: verify_dates.py [--repo REPO] [--registry FILE] [--today YYYY-MM-DD]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from breakline import skillcli  # noqa: E402


def main() -> int:
    p = skillcli.parser(__doc__)
    skillcli.add_common(p, repo=False)
    p.add_argument("--repo", help="limit the check to records that match this repo")
    args = p.parse_args()

    import datetime as dt
    from breakline import registry, scanner

    today = skillcli.today(args)
    reg = skillcli.registry_path(args)
    changes = registry.load(reg)
    meta = skillcli.registry_meta(reg)
    hits: dict[str, list[str]] = {}
    if args.repo:
        for f in scanner.scan(skillcli.repo_path(args), changes):
            hits.setdefault(f.change_id, []).append(f"{f.path}:{f.line}")

    def row(c):
        return {"id": c.id, "provider": c.provider, "symbol": c.symbol, "kind": c.kind,
                "effective_date": c.effective_date.isoformat(), "days_left": c.days_left(today),
                "evidence_url": c.evidence_url, "evidence_quote": c.evidence_quote, "found_in_repo": hits.get(c.id, [])}

    unverified = [row(c) for c in changes if c.status != "verified"]
    unverified.sort(key=lambda r: (not r["found_in_repo"], r["effective_date"]))
    age = None
    try:
        age = (today - dt.date.fromisoformat(meta["registry_version"])).days
    except (KeyError, TypeError, ValueError):
        pass
    must_check = [r for r in unverified if r["found_in_repo"]] if args.repo else unverified
    skillcli.emit({
        "ok": True, "today": today.isoformat(), **meta, "registry_age_days": age,
        "unverified_count": len(unverified), "must_check_now": must_check, "unverified": unverified,
        "verified": [row(c) for c in changes if c.status == "verified"],
        "rule": "Do not state an unverified date as fact. Read the evidence_url first, or say the date is unconfirmed.",
    })
    skillcli.log(f"{len(unverified)} unverified record(s); {len(must_check)} to confirm now (registry age: {age} days)")
    for r in must_check:
        skillcli.log(f"  {r['symbol']} {r['effective_date']}  {r['evidence_url']}")
    return 0


if __name__ == "__main__":
    skillcli.run(main)
