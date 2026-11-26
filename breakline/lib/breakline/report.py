"""Render the PR body: what breaks, what changed, and the parity proof."""
from __future__ import annotations

import datetime as dt

from .codemod import PatchSet
from .parity import ParityReport
from .registry import Change

LABEL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "not_run": "NOT RUN"}


def _pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def summarize(ps: PatchSet, parity: list[ParityReport]) -> dict:
    """The one place that decides the overall verdict. Parity that never ran is never a pass."""
    pairs = {(s.finding.symbol, s.replacement) for s in ps.patched}
    reported = {(p.old_model, p.new_model) for p in parity}
    missing = sorted(pairs - reported)
    simulated = any(p.simulated for p in parity)
    if any(p.verdict == "fail" for p in parity):
        verdict = "fail"
    elif missing or not parity:
        verdict = "not_run"
    elif any(p.verdict == "warn" for p in parity):
        verdict = "warn"
    else:
        verdict = "pass"
    blockers = []
    if verdict == "fail":
        blockers.append("parity failed")
    if missing:
        blockers.append("parity has not run for: " + ", ".join(f"{a} -> {b}" for a, b in missing))
    if verdict == "warn":
        blockers.append("parity passed with warnings")
    if simulated:
        blockers.append("parity ran in simulated mode, which is not evidence")
    pending = [s for s in ps.patched if not s.applied]
    if pending:
        blockers.append(f"{len(pending)} proposed site(s) are not applied yet")
    return {"verdict": verdict, "simulated": simulated, "missing_pairs": missing, "blockers": blockers,
            "ready_for_review": verdict == "pass" and not simulated and not pending and bool(ps.patched)}


def render(changes: list[Change], ps: PatchSet, parity: list[ParityReport], today: dt.date) -> str:
    by_id = {c.id: c for c in changes}
    hit = sorted({s.finding.change_id for s in ps.sites}, key=lambda i: by_id[i].effective_date)
    summ = summarize(ps, parity)
    overall, simulated = summ["verdict"], summ["simulated"]
    applied = sum(1 for s in ps.patched if s.applied)
    head = (f"{applied} call site(s) migrated" if applied == len(ps.patched) and applied
            else f"{applied} of {len(ps.patched)} call site(s) migrated" if applied
            else f"{len(ps.patched)} call site(s) proposed, none applied")
    tag = LABEL[overall] + (" (simulated)" if simulated else "")
    lines = [f"## Breakline: {head}, parity {tag}", ""]
    if simulated:
        lines += ["> Parity ran in **simulated** mode. No model was called, so this result is a demo and proves nothing about the real models. Re-run with `--live` before merging.", ""]

    lines += ["### Deadlines in this repo", "", "| Change | Effective | Days left | Source |", "| --- | --- | --- | --- |"]
    for cid in hit:
        c = by_id[cid]
        flag = "" if c.status == "verified" else " (unverified)"
        lines.append(f"| `{c.symbol}` {c.kind.replace('_', ' ')} | {c.effective_date} | {c.days_left(today)} | [provider page]({c.evidence_url}){flag} |")

    lines += ["", "### Call sites", "", "| File | Line | Found | Policy | Result |", "| --- | --- | --- | --- | --- |"]
    for s in ps.sites:
        f = s.finding
        if s.status == "patched":
            res = f"`{f.symbol}` -> `{s.replacement}`" + ("" if s.applied else " (proposed)")
        else:
            res = f"{s.status}: {s.reason}"
        lines.append(f"| `{f.path}` | {f.line} | {f.context} ({f.confidence:.2f}) | {s.action} | {res} |")

    for p in parity:
        lines += ["", f"### Parity: `{p.old_model}` -> `{p.new_model}`: {LABEL[p.verdict]}" + (" (simulated)" if p.simulated else ""), "",
                  "| Check | Result | Threshold |", "| --- | --- | --- |",
                  f"| Schema-valid outputs | {_pct(p.schema_valid)} | >= {_pct(p.thresholds['min_schema_valid'])} |",
                  f"| Label agreement | {_pct(p.label_agreement)} | >= {_pct(p.thresholds['min_label_agreement'])} |",
                  f"| Max numeric drift | {_pct(p.max_numeric_drift)} | <= {_pct(p.thresholds['max_numeric_drift'])} |",
                  (f"| Cost on fixtures | ${p.cost_old:.4f} -> ${p.cost_new:.4f} ({'n/a' if p.cost_delta is None else f'{p.cost_delta:+.0%}'}) | <= +{_pct(p.thresholds['max_cost_increase'])} |"
                   if p.priced else "| Cost on fixtures | not checked: no price in prices.yaml | |"),
                  f"| Mean latency | {p.latency_old_ms:.0f} ms -> {p.latency_new_ms:.0f} ms | info |",
                  f"| Fixtures replayed | {len(p.cases)} | |"]
        for w in p.warnings:
            lines += ["", f"- Warning: {w}"]
        if any(c.fences_stripped for c in p.cases):
            lines += ["", "- Some fixtures strip ``` fences before checking, matching the app's own parser."]
        bad = [c for c in p.cases if c.notes]
        if bad:
            lines += ["", "<details><summary>Regressions</summary>", ""]
            lines += [f"- `{c.fixture_id}`: " + "; ".join(str(n) for n in c.notes) for c in bad]
            lines += ["", "</details>"]

    lines += ["", "### What to do"]
    if overall == "fail":
        lines.append("- Do not merge yet. Fix the regressions above (for example, make the app parse fenced JSON, or pin the label set in the prompt and in the fixtures), or choose another replacement.")
    elif overall == "not_run":
        lines.append("- Parity has not run for every replaced model. Run it before merging.")
    elif overall == "warn":
        lines.append("- Parity did not fail, but it raised warnings (see above). Resolve them or accept them knowingly before merging.")
    else:
        lines.append("- Every fixture checked matches on every check. Review the diff and merge when ready.")
    if simulated:
        lines.append("- This run was simulated. Replay the fixtures with `--live` first.")
    if any(s.action == "ask" for s in ps.patched):
        lines.append("- Policy marks some sites `ask`: a human approval is required before they change.")
    left = [s for s in ps.sites if s.status in ("needs_human", "no_replacement")]
    if left:
        lines.append(f"- {len(left)} site(s) still need a human or a replacement choice (see the call sites table).")
    lines += [""]
    if simulated and all(p.simulated for p in parity):
        lines.append("<sub>Simulated run: no model was called and nothing left this machine.</sub>")
    elif parity:
        lines.append("<sub>Fixtures were sent only to the model endpoints compared above, with keys read from the environment.</sub>")
    return "\n".join(lines) + "\n"
