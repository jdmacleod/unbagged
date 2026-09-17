#!/usr/bin/env python3
"""Measure local vision models against synthetic receipts, two ways.

The vision tier shipped with one model chosen by hand and no evidence. What was
known about it was learned by accident: the tier had never worked at all, because
the model returned a currency mark `receipt._decimal` rejected and one unreadable
amount voids a whole answer. Both facts are about ONE model. This says which of
them generalise.

**Synthetic only, structurally.** Every page comes from `tools/bakeoff_cases.py`,
which draws it from rows written in source. There is no flag that takes a file
and none may be added: a capture's filename alone encodes a date, and a rendered
page or a saved answer would then hold somebody's shopping. Anything this writes
still has to pass `make check-pii` before it is committed.

Two tiers, and the gap between them is the signal:

* **Tier A** drives the lane as it ships — `hmart.vision.read_receipt`, so the
  real prompt and the real schema, and then `receipt.from_reply` for the gate.
  "Does it work through the lane today?"
* **Tier B** is the same call with the schema left off, and NOTHING else
  changed — same transport, same retries, same window. "Can the model do it at
  all?" A model that fails A and passes B is capable and tripped by the schema,
  not a weak reader. The currency-mark bug would have shown up here on day one.
  A second transport written for this tier would have made every build quirk
  look like a fact about the model, which is the one thing the gap must not
  contain.

Usage:

    # which models can read a receipt at all, one page each
    python -m tools.bakeoff_vision --screen --host http://10.0.0.2:11434

    # the full matrix over the models that survived screening
    python -m tools.bakeoff_vision --models qwen3-vl:8b,gemma4:e4b --out results.json

    # what a model does without the schema in its way
    python -m tools.bakeoff_vision --models glm-ocr:latest --tier b

A remote host needs `UNBAGGED_ALLOW_REMOTE_OLLAMA=true`, which the app requires
for its own reasons. Nothing sent from here is anyone's receipt, but the
acknowledgement is the app's and this does not step around it: the check runs
before any model is discovered, so an unacknowledged host is never contacted at
all rather than being contacted and then refused a model at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tools.bakeoff_cases import CASES, CASES_BY_NAME, Case, Score, score  # noqa: E402

from unbagged.adapters.hmart import receipt as rc  # noqa: E402
from unbagged.adapters.hmart import vision  # noqa: E402
from unbagged.transcription import ollama  # noqa: E402

#: Tier B has no constrained decoding, so the shape has to be asked for in
#: words. Appended to the production prompt rather than replacing it: the
#: question has to be the same question or the two tiers are not comparable.
TIER_B_TAIL = (
    "\n\nReturn ONLY a JSON object, with no prose around it and no code fence, "
    'of the form {"lines": [{"description": "...", "amount": "..."}], '
    '"tax": "...", "balance": "..."}.'
)


@dataclass
class Result:
    """One model, one case, one tier."""

    model: str
    tier: str
    case: str
    seconds: float
    returned: int
    matched: int
    expected: int
    recall: float
    furniture: int
    strict: float
    marks: tuple[str, ...]
    total_seen: bool
    total_ok: bool
    signs_ok: bool
    accepted: bool | None
    gate_ok: bool | None
    failed: str


def vision_models(host: str, timeout: float) -> list[str]:
    """Every model on the host the server itself says can take images.

    Asked of `/api/show`, never inferred from the name, for the reason
    `ollama._can_see` gives: a text-only sibling shares a multimodal model's
    family, so the tag says nothing.
    """
    body = _get(f"{host.rstrip('/')}/api/tags", timeout)
    names = sorted(str(entry.get("name", "")) for entry in (body.get("models") or []))
    seeing = []
    for name in names:
        if not name:
            continue
        try:
            shown = _post(f"{host.rstrip('/')}/api/show", {"model": name}, timeout)
        except Exception:  # noqa: BLE001, S112 - a server too old to report capabilities is not a failure
            continue
        if "vision" in (shown.get("capabilities") or []):
            seeing.append(name)
    return seeing


def _like(case: Case) -> rc.Receipt:
    """The engine's reading of this page, for `from_reply` to check an answer against.

    Ground truth, deliberately. In the lane this comes from the deterministic
    reader, which has its own error rate on its own hazards; holding it perfect
    is what makes the score a measurement of the MODEL rather than of the pair.
    It is also the friendliest case the gate ever sees, so a refusal here is the
    model's.
    """
    return rc.Receipt(
        captures=(f"{case.name}.png",),
        lines=tuple(
            rc.ReceiptLine(description=name, amount=amount, row=index)
            for index, (name, amount) in enumerate(case.purchases, start=1)
        ),
        tax=case.tax,
        balance=case.balance,
        complete=case.balance is not None,
    )


def run_tier_a(case: Case, where: ollama.Availability) -> Result:
    """The lane as it ships: the real prompt, the real schema, the real gate."""
    started = time.monotonic()
    reply = vision.read_receipt([case.png()], where)
    seconds = time.monotonic() - started
    measured = score(reply, case, seconds)

    _gate(measured, reply, case)
    return _result(where.model, "A", measured)


def run_tier_b(case: Case, where: ollama.Availability) -> Result:
    """The same question with the schema taken away, and nothing else changed.

    Through `ollama.ask` with `schema=None`, so the transport is the one the lane
    uses: the same retry when a build rejects `think`, the same widening when an
    answer will not fit, the same reading of an answer routed through a
    reasoning channel. A second transport written here instead would have
    compared this code against that code — a build quirk would have scored as a
    model that cannot read, and the A/B gap is the whole output of the tool.

    So the window is not widened by a fixed factor any more. `ask` doubles it on
    demand, twice, which is what production does and one confound fewer.

    Unconstrained, the answer arrives however the model felt like sending it —
    fenced, prefaced, or explained afterwards. `ollama._content` locates the
    object in the text rather than assuming it is the whole of it, so the prose
    case is handled on the production path too and this tier needs no scanner of
    its own.
    """
    started = time.monotonic()
    reply = ollama.ask([case.png()], vision.PROMPT + TIER_B_TAIL, None, where)
    seconds = time.monotonic() - started
    measured = score(reply, case, seconds, "" if reply else "no json in answer")
    _gate(measured, reply, case)
    return _result(where.model, "B", measured)


def _gate(measured: Score, reply: dict | None, case: Case) -> None:
    """The lane's own verdict on this answer, recorded in two parts.

    Both halves, because `adapter._adjudicate` takes an answer only when
    `from_reply` returns one AND `foots` finds nothing wrong with it, and the
    two refuse for different reasons worth telling apart. `from_reply` refuses
    an answer whose balance disagrees with the page, or whose gross runs past
    what the page can hold. `foots` is the arithmetic: it catches the answer
    that quoted the total back correctly and lost a line on the way, which no
    amount of reading the reply can see.

    Measuring only the first is how a model returning three lines of an
    eight-line basket scores as passing a gate that would have refused it.
    """
    if reply is None or case.balance is None:
        return
    candidate = rc.from_reply(reply, _like(case))
    measured.accepted = candidate is not None
    measured.gate_ok = candidate is not None and rc.foots(candidate) is None


def _result(model: str, tier: str, measured: Score) -> Result:
    return Result(
        model=model,
        tier=tier,
        case=measured.case,
        seconds=round(measured.seconds, 1),
        returned=measured.returned,
        matched=measured.matched,
        expected=measured.expected,
        recall=round(measured.recall, 3),
        furniture=measured.furniture,
        strict=round(measured.strict, 3),
        marks=measured.marks,
        total_seen=measured.total_seen,
        total_ok=measured.total_ok,
        signs_ok=measured.signs_ok,
        accepted=measured.accepted,
        gate_ok=measured.gate_ok,
        failed=measured.failed,
    )


def _why(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__


def _get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


def _post(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


def _availability_for(model: str) -> ollama.Availability:
    """The app's own preflight, for one model.

    Through `ollama.availability` rather than by constructing an `Availability`,
    so a model that the lane would refuse is refused here too and for the same
    reason. A bake-off that skips the preflight measures something the lane
    would never run.
    """
    os.environ[ollama.MODEL_ENV] = model
    return ollama.availability()


def screen(models: list[str]) -> list[str]:
    """One ordinary page, Tier A, per model. Which of these can read a receipt?

    Cheap on purpose. The full matrix is six pages and two tiers per model, and
    a model that returns nothing on the plain page will return nothing six times
    more slowly.
    """
    case = CASES_BY_NAME["plain"]
    survivors = []
    print(f"Screening {len(models)} models on the plain page (Tier A)\n")
    print(f"{'model':<28} {'secs':>6} {'recall':>7} {'total':>6} {'gate':>5}  note")
    for model in models:
        where = _availability_for(model)
        if not where.usable:
            print(f"{model:<28} {'-':>6} {'-':>7} {'-':>6} {'-':>5}  {where.status.value}")
            continue
        result = run_tier_a(case, where)
        note = result.failed or ("marks " + "".join(result.marks) if result.marks else "")
        print(
            f"{model:<28} {result.seconds:>6.1f} {result.recall:>7.2f} "
            f"{_yn(result.total_ok):>6} {_yn(result.gate_ok):>5}  {note}"
        )
        # The screen keeps anything that read a line or saw a total. Failing the
        # gate is not disqualifying here — that is the finding Tier B explains.
        if result.matched or result.total_seen:
            survivors.append(model)
    print(f"\n{len(survivors)} of {len(models)} read something.")
    return survivors


def _yn(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def report(results: list[Result]) -> None:
    """A row per model and tier, ordered the way the gate cares about.

    Sorted by whether the printed total came back before anything else, because
    an answer without it is refused however well the lines were read.
    """
    by_key: dict[tuple[str, str], list[Result]] = {}
    for result in results:
        by_key.setdefault((result.model, result.tier), []).append(result)

    rows = []
    for (model, tier), group in by_key.items():
        # Scored over the pages that HAVE a printed total. The cut-off page is
        # the top half of a taller receipt and never had one, so counting it in
        # the denominator marked every model down for reading it correctly.
        gated = [r for r in group if r.gate_ok is not None]
        rows.append(
            {
                "model": model,
                "tier": tier,
                "totals": sum(1 for r in gated if r.total_ok),
                "of": len(gated),
                "recall": statistics.mean([r.recall for r in group]),
                "strict": statistics.mean([r.strict for r in group]),
                "furniture": sum(r.furniture for r in group),
                "accepted": sum(1 for r in gated if r.accepted),
                "gate": sum(1 for r in gated if r.gate_ok),
                "gate_of": len(gated),
                "signs": all(r.signs_ok for r in group),
                "secs": statistics.median([r.seconds for r in group]),
                "marks": "".join(sorted({m for r in group for m in r.marks})),
            }
        )
    rows.sort(key=lambda row: (-row["totals"], -row["recall"], row["secs"]))

    print()
    print(
        f"{'model':<28} {'T':<2} {'total':>6} {'acc':>6} {'gate':>6} {'recall':>7} "
        f"{'strict':>7} {'furn':>5} {'sign':>5} {'med s':>7}  marks"
    )
    for row in rows:
        print(
            f"{row['model']:<28} {row['tier']:<2} "
            f"{row['totals']:>3}/{row['of']:<2} "
            f"{row['accepted']:>3}/{row['gate_of']:<2} {row['gate']:>3}/{row['gate_of']:<2} "
            f"{row['recall']:>7.2f} {row['strict']:>7.2f} {row['furniture']:>5} "
            f"{_yn(row['signs']):>5} {row['secs']:>7.1f}  {row['marks']}"
        )
    print()
    print("total  the printed balance came back correct — the gate rests on this.")
    print("       Over the pages that print one: the cut-off page never had a total.")
    print("acc    `from_reply` accepted it (pages with a printed total only)")
    print("gate   the lane took it: accepted AND `foots` found the arithmetic sound")
    print("recall mean fraction of purchase lines found, amount exact")
    print("strict fraction of returned amounts `receipt._decimal` accepts")
    print("furn   furniture rows returned, which the prompt forbids twice")
    print("sign   every negative line came back negative")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--host",
        default=os.environ.get(ollama.HOST_ENV) or "http://localhost:11434",
        help=f"Ollama host. Defaults to ${ollama.HOST_ENV}.",
    )
    parser.add_argument(
        "--models", help="Comma-separated. Default: every vision model on the host."
    )
    parser.add_argument(
        "--cases",
        help="Comma-separated. Default: all of " + ",".join(c.name for c in CASES) + ".",
    )
    parser.add_argument("--tier", choices=("a", "b", "both"), default="both")
    parser.add_argument("--screen", action="store_true", help="One page per model, then stop.")
    parser.add_argument("--timeout", type=float, default=ollama.TIMEOUT_SECONDS)
    parser.add_argument("--out", type=Path, help="Write every measurement here as JSON.")
    parser.add_argument(
        "--from",
        dest="replay",
        type=Path,
        help="Re-render the table from a saved --out file, asking no model anything.",
    )
    args = parser.parse_args(argv)

    if args.replay:
        # A full matrix is measured in hours. Changing how it is REPORTED should
        # not cost another one.
        report([Result(**row) for row in json.loads(args.replay.read_text())])
        return 0

    os.environ[ollama.HOST_ENV] = args.host

    # The app's own gate, BEFORE anything is sent. `availability` tests the host
    # policy before it contacts the host, so asking it first is what makes this
    # tool's claim true — discovery used to reach `/api/tags` and `/api/show` on
    # an unacknowledged remote host while the docstring said it did not step
    # around the acknowledgement. The model named here is whatever is configured;
    # only the REFUSED answer is about the host, and only that one stops us.
    refusal = ollama.availability()
    if refusal.status is ollama.Reachability.REFUSED:
        print(refusal.message, file=sys.stderr)
        return 2

    try:
        available = vision_models(args.host, ollama.PREFLIGHT_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the host being down is a message, not a traceback
        print(f"No model answered at {args.host} ({_why(exc)}).", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",")] if args.models else available
    unknown = [m for m in models if m not in available]
    if unknown:
        print(f"Not vision-capable on {args.host}: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if not models:
        print(f"{args.host} has no vision model pulled.", file=sys.stderr)
        return 2

    if args.screen:
        screen(models)
        return 0

    cases = [CASES_BY_NAME[n.strip()] for n in args.cases.split(",")] if args.cases else list(CASES)
    tiers = ("A", "B") if args.tier == "both" else (args.tier.upper(),)

    results: list[Result] = []
    for model in models:
        where = _availability_for(model)
        print(f"\n{model}  ({where.status.value})", flush=True)
        if not where.usable:
            print(f"  skipped: {where.message or where.status.value}")
            continue
        for case in cases:
            for tier in tiers:
                result = run_tier_a(case, where) if tier == "A" else run_tier_b(case, where)
                results.append(result)
                print(
                    f"  {tier} {case.name:<10} {result.seconds:>6.1f}s "
                    f"recall {result.recall:.2f}  total {_yn(result.total_ok)}  "
                    f"acc {_yn(result.accepted)}  gate {_yn(result.gate_ok)}  {result.failed}",
                    flush=True,
                )
                # Written as it goes. A matrix this size is measured in hours and
                # a host that goes away should not cost every answer before it.
                if args.out:
                    args.out.write_text(json.dumps([asdict(r) for r in results], indent=1))

    report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
