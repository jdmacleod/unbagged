"""What to ask a local vision model about a receipt, and how to read its answer.

The question belongs here, not in `unbagged.transcription`. That package reads
pixels and says in its own docstring that nothing in it knows what a receipt is
— a claim a receipt-shaped prompt and a `tax`/`balance` schema living inside it
made false. `transcription.ollama` is the transport; this is the question.

The answer is never trusted. `receipt.from_reply` puts it through the total the
engine read off the page, which is the one number the model had no hand in.
"""

from __future__ import annotations

from unbagged.transcription import ollama

#: Flat on purpose. Quantised models in this size class return empty arrays at
#: intermediate levels of a nested schema, so the whole receipt is one object
#: with one list in it.
RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["description", "amount"],
            },
        },
        "tax": {"type": "string"},
        "balance": {"type": "string"},
    },
    "required": ["lines", "tax", "balance"],
}

PROMPT = (
    "This is a screen capture of a supermarket receipt. Transcribe every "
    "purchase line exactly as printed: the product description in the middle "
    "column and its amount in the right column. Keep a minus sign where one is "
    "printed — a negative line is a discount or a cancelled item and it matters. "
    "Do not merge lines. Do not include the TAX line or the BALANCE line or the "
    "payment method among the purchases; report the tax and the balance in "
    "their own fields. Report amounts as decimal numbers with two places and no "
    "currency symbol. Ignore anything written over the receipt by hand, and "
    "ignore the card details at the bottom entirely."
)


def read_receipt(pages: list[bytes], where: ollama.Availability, opener=None) -> dict | None:
    """Ask the model to read one receipt out of its captures."""
    return ollama.ask(pages, PROMPT, RECEIPT_SCHEMA, where, opener=opener)
