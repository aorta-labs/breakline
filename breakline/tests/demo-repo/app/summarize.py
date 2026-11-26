"""Weekly account summary written by Claude."""
import json

import anthropic

client = anthropic.Anthropic()
SUMMARY_MODEL = "claude-opus-4-5-20251101"


def summarize(account_notes: str) -> dict:
    msg = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": 'Return JSON {"health": "green|amber|red", "summary": "..."} for:\n' + account_notes}],
    )
    return json.loads(msg.content[0].text)
