# The fallback adapter

Not a retailer. This is what runs when nothing else recognises a response.

## Why it exists

A retailer that answers a right-to-know request with a letter and no data has
not disclosed the specific pieces of personal information it holds, nor anything
else. Refusing to parse that would hide the finding behind an error message.

Small retailers are the likely case — a business with one store and a
spreadsheet is far more likely to send a PDF letter than a structured export.
The absence of data is exactly what the compliance view exists to record.

## What it will and will not claim

It marks a category `PARTIAL` when the response contains wording associated with
that category, quoting the sentence as evidence, and `ABSENT` otherwise.

**It never marks anything `PROVIDED`.** Keyword matching can show that a topic
was mentioned. It cannot show that the question was answered, and treating the
two as the same would put a green cell next to "we take your privacy seriously".
The strongest honest claim from a keyword is "this came up — read it yourself",
which is what `PARTIAL` plus a quoted sentence says.

Every parse emits a warning saying the response was read as unstructured text,
and a `clarification` follow-up telling the reader to check it by hand. Nothing
here should be mistaken for a real parse.

## What it does not do

No purchases, no identifiers, no inferred attributes. Extracting those from prose
would mean guessing, and a wrong purchase history is worse than none: it looks
authoritative. If a retailer sends data worth extracting, that retailer deserves
an adapter — see `docs/writing-an-adapter.md`.

## Selection

`fallback = True`, and `registry.select()` only consults fallbacks when no real
adapter scores above zero. An explicit flag rather than a low confidence score:
"the fallback always scores lower than every real adapter" is an invariant living
in two files, and it would break silently the first time a retailer's format
degraded and its own adapter's confidence dropped.
