"""Overlap between golden utterances and the agent's training phrases.

Two rules, both on normalised text (casefolded, "&" read as "and",
punctuation dropped, whitespace collapsed):

* **verbatim**: the normalised utterance equals a normalised training phrase.
  Dev-set cases that match are labelled `verbatim_in_training: true`, and the
  report gives every headline metric with and without them.
* **near**: verbatim, or a character similarity (difflib ratio) of at least
  NEAR_THRESHOLD. The held-out set must have no near match with any training
  phrase or any dev-set utterance; candidates that had one were dropped when
  the set was built. An overlap introduced later by an agent change is listed
  in HELDOUT_KNOWN_OVERLAP (the held-out file itself is never edited).

Usage:
    uv run python evals/leakage.py            # list matches, check the labels
    uv run python evals/leakage.py --strict   # exit 1 if a label or the held-out rule is off

tests/test_evals_metrics.py runs the same checks.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
AGENT_DIR = REPO_ROOT / "dialogflow" / "agent"
DEV_CASES = EVALS_DIR / "golden" / "nlu_single_turn.yaml"
DEV_CONVERSATIONS = EVALS_DIR / "golden" / "conversations.yaml"
HELDOUT = EVALS_DIR / "golden" / "heldout.yaml"

NEAR_THRESHOLD = 0.85

# Documented exceptions to the held-out rule: (case id, exact text) -> reason.
# heldout.yaml is never edited, so when a later agent change happens to add a
# held-out utterance as a training phrase, the overlap is recorded here and the
# reports give the held-out numbers with and without the case. Any other near
# match still fails the check.
HELDOUT_KNOWN_OVERLAP: dict[tuple[str, str], str] = {
    ("ho-complete-01", "thats all thanks"): (
        "added as an order.complete training phrase in the v1.1.0 agent update "
        "(completion phrases round; the private repository it was written in never "
        "contained the held-out set); "
        "the case already passed against the v1.0.0 agent"
    ),
}


def normalize(text: str) -> str:
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def training_phrases(agent_dir: Path = AGENT_DIR) -> list[tuple[str, str]]:
    """(intent, phrase) for every training phrase in a Dialogflow ES export."""
    out: list[tuple[str, str]] = []
    for path in sorted((agent_dir / "intents").glob("*_usersays_*.json")):
        intent = path.name.rsplit("_usersays_", 1)[0]
        for example in json.loads(path.read_text(encoding="utf-8")):
            out.append((intent, "".join(part["text"] for part in example["data"])))
    return out


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def best_match(text: str, corpus: Iterable[str]) -> tuple[float, str | None]:
    """Highest similarity of `text` against `corpus` (1.0 for a verbatim match)."""
    norm = normalize(text)
    best: tuple[float, str | None] = (0.0, None)
    for other in corpus:
        other_norm = normalize(other)
        if other_norm == norm:
            return 1.0, other
        ratio = difflib.SequenceMatcher(None, norm, other_norm).ratio()
        if ratio > best[0]:
            best = (ratio, other)
    return best


def verbatim_case_ids(cases: list[dict[str, Any]], phrases: Iterable[str]) -> set[str]:
    """Ids of cases whose text is a verbatim (normalised) training phrase."""
    norms = {normalize(p) for p in phrases}
    return {c["id"] for c in cases if normalize(c["text"]) in norms}


def near_matches(
    texts: Iterable[tuple[str, str]], corpus: list[str], threshold: float = NEAR_THRESHOLD
) -> list[tuple[str, str, str | None, float]]:
    """(label, text, closest corpus entry, ratio) for every text at or above `threshold`."""
    hits = []
    for label, text in texts:
        ratio, other = best_match(text, corpus)
        if ratio >= threshold:
            hits.append((label, text, other, round(ratio, 3)))
    return hits


def unexpected_heldout_hits(
    hits: list[tuple[str, str, str | None, float]],
) -> list[tuple[str, str, str | None, float]]:
    """Near matches that are not documented in HELDOUT_KNOWN_OVERLAP."""
    return [h for h in hits if (h[0], h[1]) not in HELDOUT_KNOWN_OVERLAP]


def heldout_texts(
    cases: list[dict[str, Any]], convs: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    texts = [(c["id"], c["text"]) for c in cases]
    for conv in convs:
        texts += [(f"{conv['id']} turn {i}", t["text"]) for i, t in enumerate(conv["turns"], 1)]
    return texts


def dev_texts(cases: list[dict[str, Any]], convs: list[dict[str, Any]]) -> list[str]:
    return [c["text"] for c in cases] + [t["text"] for conv in convs for t in conv["turns"]]


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(EVALS_DIR))
    import nlu_metrics as m

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--strict", action="store_true", help="exit 1 when a check fails")
    args = parser.parse_args(argv)

    phrases = [p for _, p in training_phrases()]
    dev_cases = m.load_cases(DEV_CASES)
    dev_convs = m.load_conversations(DEV_CONVERSATIONS)
    ok = True

    computed = verbatim_case_ids(dev_cases, phrases)
    labelled = {c["id"] for c in dev_cases if c.get("verbatim_in_training")}
    print(f"training phrases: {len(phrases)}")
    print(f"dev cases verbatim in training: {len(computed)} of {len(dev_cases)}")
    for case in dev_cases:
        if case["id"] in computed:
            print(f"  {case['id']}: {case['text']!r}")
    if computed != labelled:
        ok = False
        print(
            f"LABEL MISMATCH: missing {sorted(computed - labelled)}, extra {sorted(labelled - computed)}"
        )

    if HELDOUT.exists():
        ho_cases = m.load_cases(HELDOUT)
        ho_convs = m.load_conversations(HELDOUT)
        corpus = phrases + dev_texts(dev_cases, dev_convs)
        hits = near_matches(heldout_texts(ho_cases, ho_convs), corpus)
        print(
            f"held-out: {len(ho_cases)} cases, {len(ho_convs)} conversations, "
            f"{len(hits)} near matches (threshold {NEAR_THRESHOLD})"
        )
        unexpected = unexpected_heldout_hits(hits)
        for label, text, other, ratio in hits:
            known = HELDOUT_KNOWN_OVERLAP.get((label, text))
            if known:
                print(f"  KNOWN {label}: {text!r} ~ {other!r} ({ratio}): {known}")
            else:
                print(f"  NEAR {label}: {text!r} ~ {other!r} ({ratio})")
        if unexpected:
            ok = False
    return 0 if ok or not args.strict else 1


if __name__ == "__main__":
    sys.exit(main())
