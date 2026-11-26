"""Support-ticket triage: returns {"intent", "amount", "priority"} as JSON."""
import json
import os

from openai import OpenAI

client = OpenAI()
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "gpt-4o-2024-08-06")

SYSTEM = (
    "Classify the ticket. Reply with JSON only: "
    '{"intent": one of [refund_request, billing_question, bug_report, other], '
    '"amount": number or 0, "priority": one of [low, medium, high]}'
)


def triage(ticket: str) -> dict:
    resp = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        temperature=0,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": ticket}],
    )
    return json.loads(resp.choices[0].message.content)
