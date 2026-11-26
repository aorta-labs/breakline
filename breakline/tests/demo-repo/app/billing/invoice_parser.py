"""Invoice extraction. Policy marks billing/ as deny: Breakline must not touch it."""
from openai import OpenAI

client = OpenAI()


def parse_invoice(text: str) -> str:
    r = client.chat.completions.create(model="gpt-4.1-2025-04-14", messages=[{"role": "user", "content": text}])
    return r.choices[0].message.content
