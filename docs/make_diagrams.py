#!/usr/bin/env python3
"""Draw the README diagrams as light and dark SVGs. Run: python3 make_diagrams.py [OUTDIR]

One visual rule across all four: a line is the flow, a hollow point is a script
deciding, and the rose point is where it stops for a person. 480 units wide, so
labels stay readable on a phone.
"""
import sys
from html import escape
from pathlib import Path

THEMES = {
    "light": dict(bg="#FFFFFF", ink="#111111", muted="#52525B", line="#D4D4D8", box="#FAFAFA", rose="#E11D48", warn="#92400E", ok="#2D704C"),
    "dark": dict(bg="#0D1117", ink="#F4F3EF", muted="#A1A1AA", line="#3F3F46", box="#161B22", rose="#FB7185", warn="#FBBF24", ok="#74BE91"),
}
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
W = 480


class Svg:
    def __init__(self, t):
        self.t, self.o = t, []

    def text(self, x, y, s, size=13, color="ink", mono=False, weight=400, anchor="start"):
        self.o.append(f'<text x="{x}" y="{y}" font-family="{MONO if mono else SANS}" font-size="{size}" font-weight="{weight}" '
                      f'text-anchor="{anchor}" fill="{self.t[color]}">{escape(s)}</text>')

    def path(self, d, color="ink", width=2, dash=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.o.append(f'<path d="{d}" fill="none" stroke="{self.t[color]}" stroke-width="{width}"{extra}/>')

    def arrow(self, x, y):
        self.path(f"M{x - 5} {y - 6}L{x} {y}L{x + 5} {y - 6}")

    def box(self, x, y, w, h, dash=None, fill="box"):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{self.t[fill]}" stroke="{self.t["line"]}"{extra}/>')

    def gate(self, x, y):
        self.o.append(f'<circle cx="{x}" cy="{y}" r="9" fill="#E11D48"/>')

    def step(self, x, y):
        self.o.append(f'<circle cx="{x}" cy="{y}" r="7" fill="{self.t["bg"]}" stroke="{self.t["ink"]}" stroke-width="3"/>')

    def done(self, height, title):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img" aria-label="{escape(title)}">'
                f'<rect width="{W}" height="{height}" rx="16" fill="{self.t["bg"]}"/>' + "".join(self.o) + "</svg>\n")


def workflow(t):
    s = Svg(t)
    rows = [  # title, detail, script (None: waits for a person)
        ("Preflight", "Policy and fixtures present?", "SKILL.md"),
        ("Check the dates", "Which records are unverified?", "verify_dates.py"),
        ("Scan", "Retiring calls, soonest first", "scan.py"),
        ("Choose replacements", "You name each new model", None),
        ("Plan", "A diff. No source file changes", "plan_patch.py"),
        ("Prove", "Your fixtures on both models", "parity.py"),
        ("Approve", "You read the diff and say yes", None),
        ("Apply and report", "Patch, then pr_body.md", "report.py"),
    ]
    y = [40 + i * 66 for i in range(len(rows))]
    s.path(f"M44 {y[0]}V{y[-1]}")
    for (title, detail, script), cy in zip(rows, y):
        (s.step if script else s.gate)(44, cy)
        s.text(70, cy - 3, title, 15, weight=600)
        s.text(70, cy + 16, detail, 13, "muted")
        s.text(456, cy - 3, script or "you", 12, "muted" if script else "rose", mono=bool(script), weight=400 if script else 600, anchor="end")
    # a failed parity run goes back to planning
    s.path(f"M262 {y[5] + 11}H290V{y[4] + 11}H270", "warn", 1.5, "5 4")
    s.path(f"M277 {y[4] + 6}L270 {y[4] + 11}L277 {y[4] + 16}", "warn", 1.5)
    s.text(300, y[4] + 33, "exit 2: fix", 12, "warn")
    s.text(300, y[4] + 49, "and replay", 12, "warn")
    foot = y[-1] + 54
    s.step(44, foot - 4)
    s.text(70, foot, "A script decides", 12, "muted")
    s.gate(214, foot - 4)
    s.text(234, foot, "Stops and waits for you", 12, "muted")
    return s.done(foot + 24, "Breakline workflow: preflight, check dates, scan, you choose replacements, plan, prove, you approve, apply and report. A failed parity run returns to planning.")


def boundary(t):
    s = Svg(t)
    s.box(16, 16, 448, 180, dash="6 5")
    s.text(34, 46, "Your machine or CI runner", 15, weight=600)
    for i, (a, b) in enumerate([("Repository", "your code"), ("Scripts", "scan, plan, parity"), ("API keys", "from environment")]):
        x = 34 + i * 142
        s.box(x, 64, 128, 62, fill="bg")
        s.text(x + 64, 90, a, 14, weight=600, anchor="middle")
        s.text(x + 64, 110, b, 11, "muted", anchor="middle")
    s.text(34, 154, "Claude Code runs here. The Claude model does not.", 12, "muted")
    s.text(34, 174, "In the Claude apps, this box is a hosted sandbox.", 12, "muted")
    outs = [
        ("Claude model", "Files Claude opens and what the scripts print.", "That can include code and fixtures.", "Whenever Claude runs the skill"),
        ("The two models being compared", "Fixture prompts, sent with your API key.", "Nothing else from the repository.", "Only with parity.py --live"),
        ("Provider documentation pages", "A page request to confirm a date.", "No repository content.", "Only if Claude has web tools"),
    ]
    s.path("M34 196V496")
    for i, (head, a, b, when) in enumerate(outs):
        y = 224 + i * 112
        s.path(f"M34 {y + 48}H62")
        s.path(f"M56 {y + 43}L63 {y + 48}L56 {y + 53}")
        s.box(66, y, 398, 96)
        s.text(84, y + 25, head, 14, weight=600)
        s.text(84, y + 46, a, 12, "muted")
        s.text(84, y + 63, b, 12, "muted")
        s.text(84, y + 83, when, 11, mono=True)
    return s.done(560, "What leaves your machine: the Claude model receives the files Claude opens and script output; the compared models receive fixture prompts only in live parity; provider documentation pages are fetched to confirm dates.")


def outcomes(s, y, height, items):
    """Three columns, each an outcome with a few short lines."""
    for x, name, color, lines in items:
        s.arrow(x, y)
        s.box(x - 70, y + 2, 140, height)
        s.text(x, y + 29, name, 15, color, mono=True, weight=700, anchor="middle")
        for i, line in enumerate(lines):
            s.text(x, y + 54 + i * 18, line, 12, "muted", anchor="middle")


def policy(t):
    s = Svg(t)
    s.box(150, 16, 180, 46)
    s.text(240, 45, "A call site", 14, weight=600, anchor="middle")
    s.path("M240 62V94")
    s.arrow(240, 94)
    s.box(92, 96, 296, 62)
    s.text(240, 122, "First matching rule in policy.yaml", 14, weight=600, anchor="middle")
    s.text(240, 143, "no rule matches: ask", 12, "muted", mono=True, anchor="middle")
    s.path("M240 158V186M86 214V186H394V214M240 186V214")
    outcomes(s, 214, 128, [
        (86, "allow", "ok", ("In the plan.", "--apply writes it.")),
        (240, "ask", "rose", ("In the plan.", "Written only with", "--include-ask,", "after you approve.")),
        (394, "deny", "warn", ("Reported.", "Never edited,", "whatever the flags.")),
    ])
    return s.done(362, "Policy decision: the first matching rule marks a site allow, ask or deny. With no matching rule it is ask.")


def verdict(t):
    s = Svg(t)
    s.text(24, 38, "Each fixture, old model against new", 15, weight=600)
    checks = [("Schema-valid", "at least 100%"), ("Label agreement", "at least 95%"), ("Numeric drift", "at most 5%"),
              ("Regex", "every fixture matches"), ("Cost", "at most +25%"), ("Latency", "shown, never fails")]
    s.box(16, 56, 448, len(checks) * 34 + 12)
    for i, (name, rule) in enumerate(checks):
        y = 84 + i * 34
        s.text(36, y, name, 13)
        s.text(444, y, rule, 12, "muted", mono=True, anchor="end")
        if i < len(checks) - 1:
            s.path(f"M36 {y + 12}H444", "line", 1)
    s.text(24, 292, "Defaults. Change them under parity: in policy.yaml.", 12, "muted")
    s.path("M240 306V330M86 358V330H394V358M240 330V358")
    outcomes(s, 358, 108, [
        (86, "fail", "warn", ("A threshold failed.", "Exit code 2.", "Stays a draft.")),
        (240, "warn", "ink", ("Something unchecked", "or costlier.", "Stays a draft.")),
        (394, "pass", "ok", ("Every check ran", "and passed.")),
    ])
    s.path("M394 468V500")
    s.arrow(394, 500)
    s.box(16, 502, 448, 92)
    s.text(36, 533, "Ready for review needs all of this", 14, weight=600)
    s.text(36, 556, "A live pass for every replaced model, with the", 12, "muted")
    s.text(36, 574, "patch applied. A simulated run never counts.", 12, "muted")
    return s.done(610, "Parity verdict: six checks produce fail, warn or pass. A pull request is ready only after a live pass for every replaced model with the patch applied.")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
    out.mkdir(parents=True, exist_ok=True)
    for draw in (workflow, boundary, policy, verdict):
        for theme, tokens in THEMES.items():
            (out / f"{draw.__name__}-{theme}.svg").write_text(draw(tokens), encoding="utf-8")
