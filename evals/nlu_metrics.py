"""Pure scoring, metrics and report code for the NLU eval harness.

No network, no Google imports: everything here works on plain dicts so it can
be unit tested and re-run offline on a stored results.json (--rescore).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MENU = (
    "Bunny Chow",
    "Boerewors Roll",
    "Pap & Chakalaka",
    "Bobotie",
    "Vetkoek",
    "Kota",
    "Malva Pudding",
    "Milk Tart",
    "Sosaties",
)

INTENTS = (
    "Default Welcome Intent",
    "Default Fallback Intent",
    "new.order",
    "order.add",
    "order_remove",
    "order.complete",
    "track.order",
    "track.order - more",
)

ERROR_INTENT = "<error>"
NO_INTENT = "<none>"

_CASE_KEYS = {
    "id",
    "text",
    "expected_intent",
    "expected_food_Item",
    "expected_number",
    "allow_slot_prompt",
    "input_contexts",
    "context",
    "tags",
    "expect_reply",
    "verbatim_in_training",
}


# --------------------------------------------------------------- observation


@dataclass
class Observation:
    """What the agent returned for one detect_intent call (already normalised)."""

    intent: str
    confidence: float = 0.0
    food_Item: list[str] = field(default_factory=list)
    number: list[int | float] = field(default_factory=list)
    all_required_params_present: bool = True
    reply: str = ""
    latency_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _to_number(value: Any) -> int | float | None:
    """Dialogflow numbers arrive as floats (2.0) or strings; ints when whole."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            if value.is_integer():
                return int(value)
        return value
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalize_food(value: Any) -> list[str]:
    """food_Item may be a list, a single string, or "" when not filled."""
    out = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def normalize_number(value: Any) -> list[int | float]:
    """number is a list for order.add, a scalar for track.order, "" when empty."""
    out = []
    for item in _as_list(value):
        n = _to_number(item)
        if n is not None:
            out.append(n)
    return out


def observation_from_query_result(
    query_result: dict[str, Any], latency_ms: float | None = None
) -> Observation:
    """Build an Observation from MessageToDict(query_result) (camelCase keys)."""
    params = query_result.get("parameters") or {}
    intent = (query_result.get("intent") or {}).get("displayName") or NO_INTENT
    return Observation(
        intent=intent,
        confidence=float(query_result.get("intentDetectionConfidence") or 0.0),
        food_Item=normalize_food(params.get("food_Item")),
        number=normalize_number(params.get("number")),
        # proto3 omits false booleans from MessageToDict output
        all_required_params_present=bool(query_result.get("allRequiredParamsPresent", False)),
        reply=str(query_result.get("fulfillmentText") or ""),
        latency_ms=latency_ms,
    )


def error_observation(message: str, latency_ms: float | None = None) -> Observation:
    return Observation(intent=ERROR_INTENT, error=message, latency_ms=latency_ms)


# ------------------------------------------------------------------- golden


class GoldenSetError(ValueError):
    """The golden file is malformed."""


def expected_number(value: Any) -> list[int | float] | None:
    """Expected number label: None = not checked; scalar -> [scalar]."""
    if value is None:
        return None
    return normalize_number(value)


def expected_food(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise GoldenSetError(f"expected_food_Item must be a list, got {value!r}")
    return [str(v) for v in value]


def _validate_turn(turn: dict[str, Any], where: str) -> None:
    unknown = set(turn) - _CASE_KEYS
    if unknown:
        raise GoldenSetError(f"{where}: unknown keys {sorted(unknown)}")
    if not isinstance(turn.get("text"), str) or not turn["text"].strip():
        raise GoldenSetError(f"{where}: missing text")
    if turn.get("expected_intent") not in INTENTS:
        raise GoldenSetError(
            f"{where}: expected_intent {turn.get('expected_intent')!r} is not one of {INTENTS}"
        )
    food = expected_food(turn.get("expected_food_Item"))
    for item in food or []:
        if item not in MENU:
            raise GoldenSetError(f"{where}: {item!r} is not an exact menu name")
    num = turn.get("expected_number")
    if num is not None and expected_number(num) == [] and num != []:
        raise GoldenSetError(f"{where}: expected_number {num!r} is not numeric")
    if not isinstance(turn.get("verbatim_in_training", False), bool):
        raise GoldenSetError(f"{where}: verbatim_in_training must be true or false")
    for key in ("input_contexts", "context", "tags"):
        val = turn.get(key)
        if val is not None and (
            not isinstance(val, list) or not all(isinstance(v, str) for v in val)
        ):
            raise GoldenSetError(f"{where}: {key} must be a list of strings")


def validate_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for i, case in enumerate(cases):
        cid = case.get("id")
        if not isinstance(cid, str) or not cid:
            raise GoldenSetError(f"case #{i}: missing id")
        if cid in seen:
            raise GoldenSetError(f"duplicate case id {cid!r}")
        seen.add(cid)
        _validate_turn(case, cid)
    return cases


def validate_conversations(convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for i, conv in enumerate(convs):
        cid = conv.get("id")
        if not isinstance(cid, str) or not cid:
            raise GoldenSetError(f"conversation #{i}: missing id")
        if cid in seen:
            raise GoldenSetError(f"duplicate conversation id {cid!r}")
        seen.add(cid)
        turns = conv.get("turns")
        if not isinstance(turns, list) or not turns:
            raise GoldenSetError(f"{cid}: needs a non-empty turns list")
        for j, turn in enumerate(turns):
            _validate_turn(turn, f"{cid} turn {j + 1}")
    return convs


def golden_fingerprint(obj: Any) -> str:
    """Stable hash of parsed golden data (independent of line endings/format)."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load_yaml(path: Path) -> Any:
    import yaml  # dev dependency; imported lazily so metrics code needs no yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise GoldenSetError(f"{path}: expected a 'cases' list")
    return validate_cases(cases)


def load_conversations(path: Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    convs = data.get("conversations") if isinstance(data, dict) else data
    if not isinstance(convs, list):
        raise GoldenSetError(f"{path}: expected a 'conversations' list")
    return validate_conversations(convs)


# ------------------------------------------------------------------ scoring


def score_turn(expected: dict[str, Any], obs: Observation) -> dict[str, Any]:
    """Score one observed turn against its labels.

    Returns a dict of checks; a check is None when it does not apply.
      intent_ok     predicted intent == expected_intent
      food_ok       food_Item multiset match (order-insensitive)
      number_ok     quantities match in utterance order (int-normalised)
      prompt_ok     False when the agent prompted for a slot but the label
                    does not allow a slot prompt
      reply_ok      reply text non-empty, when expect_reply is set
      passed        all applicable checks True
    """
    exp_intent = expected["expected_intent"]
    allow_prompt = bool(expected.get("allow_slot_prompt", False))
    prompted = not obs.all_required_params_present
    intent_ok = obs.intent == exp_intent

    exp_food = expected_food(expected.get("expected_food_Item"))
    food_ok = None
    if exp_food is not None:
        food_ok = Counter(obs.food_Item) == Counter(exp_food)

    exp_num = expected_number(expected.get("expected_number"))
    number_ok = None
    if exp_num is not None:
        if allow_prompt and prompted and not obs.number:
            number_ok = None  # the agent asked for it, which the label allows
        else:
            number_ok = list(obs.number) == list(exp_num)

    prompt_ok = allow_prompt or not prompted
    reply_ok = None
    if expected.get("expect_reply"):
        reply_ok = bool(obs.reply.strip())

    passed = (
        intent_ok
        and food_ok is not False
        and number_ok is not False
        and prompt_ok
        and reply_ok is not False
        and obs.error is None
    )
    return {
        "intent_ok": intent_ok,
        "food_ok": food_ok,
        "number_ok": number_ok,
        "prompted": prompted,
        "prompt_ok": prompt_ok,
        "reply_ok": reply_ok,
        "passed": passed,
    }


def describe_failure(expected: dict[str, Any], obs: Observation, score: dict[str, Any]) -> str:
    parts = []
    if obs.error:
        parts.append(f"error: {obs.error}")
    if not score["intent_ok"]:
        parts.append(f"intent {expected['expected_intent']} -> {obs.intent}")
    if score["food_ok"] is False:
        parts.append(
            f"food_Item {sorted(expected.get('expected_food_Item') or [])} -> {sorted(obs.food_Item)}"
        )
    if score["number_ok"] is False:
        parts.append(
            f"number {expected_number(expected.get('expected_number'))} -> {list(obs.number)}"
        )
    if not score["prompt_ok"]:
        parts.append("unexpected slot prompt")
    if score["reply_ok"] is False:
        parts.append("empty reply")
    return "; ".join(parts)


# ------------------------------------------------------------------ metrics


def _safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def classification_metrics(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Per-label precision/recall/F1/support, accuracy, macro F1, confusion.

    `pairs` are (expected, predicted). Labels that only ever appear as
    predictions (e.g. "<error>") get support 0 and recall None; they still
    show up in the confusion matrix and as false positives.
    """
    pairs = list(pairs)
    labels = sorted({e for e, _ in pairs} | {p for _, p in pairs})
    confusion: dict[str, dict[str, int]] = {e: defaultdict(int) for e in labels}
    for exp, pred in pairs:
        confusion[exp][pred] += 1

    per_label: dict[str, dict[str, Any]] = {}
    for label in labels:
        tp = sum(1 for e, p in pairs if e == label and p == label)
        fp = sum(1 for e, p in pairs if e != label and p == label)
        fn = sum(1 for e, p in pairs if e == label and p != label)
        support = tp + fn
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, support)
        if precision is None or recall is None:
            f1 = None
        elif precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    supported = [m for m in per_label.values() if m["support"] > 0]
    # macro F1 over labels with support; an F1 of None there means precision
    # was undefined (never predicted) -> counts as 0.
    macro_f1 = _safe_div(sum((m["f1"] or 0.0) for m in supported), len(supported))
    correct = sum(1 for e, p in pairs if e == p)
    return {
        "n": len(pairs),
        "accuracy": _safe_div(correct, len(pairs)),
        "macro_f1": macro_f1,
        "per_intent": per_label,
        "labels": labels,
        "confusion": {e: dict(row) for e, row in confusion.items()},
    }


def percentile(values: Iterable[float], p: float) -> float | None:
    """Linear-interpolated percentile (same as numpy's default)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    k = (len(vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(vals[int(k)])
    return float(vals[lo] + (vals[hi] - vals[lo]) * (k - lo))


def latency_stats(values: Iterable[float | None]) -> dict[str, Any]:
    vals = [v for v in values if v is not None]
    return {
        "count": len(vals),
        "p50_ms": percentile(vals, 50),
        "p95_ms": percentile(vals, 95),
        "max_ms": max(vals) if vals else None,
        "mean_ms": (sum(vals) / len(vals)) if vals else None,
    }


def slot_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """food_Item / number accuracy on records whose label checks that slot.

    accuracy           extraction match on every case that labels the slot,
                       regardless of whether the intent was right (a slot can
                       be extracted correctly under the wrong intent)
    accuracy_given_intent  only cases whose intent was right (what the
                       server would actually act on)
    """
    out = {}
    for slot, key in (("food_Item", "food_ok"), ("number", "number_ok")):
        checked = [r for r in records if r["score"][key] is not None]
        correct = [r for r in checked if r["score"][key]]
        cond = [r for r in checked if r["score"]["intent_ok"]]
        cond_ok = [r for r in cond if r["score"][key]]
        out[slot] = {
            "checked": len(checked),
            "correct": len(correct),
            "accuracy": _safe_div(len(correct), len(checked)),
            "checked_given_intent": len(cond),
            "accuracy_given_intent": _safe_div(len(cond_ok), len(cond)),
        }
    both = [
        r
        for r in records
        if r["score"]["food_ok"] is not None or r["score"]["number_ok"] is not None
    ]
    both_ok = [
        r
        for r in both
        if r["score"]["food_ok"] is not False and r["score"]["number_ok"] is not False
    ]
    out["all_slots"] = {
        "checked": len(both),
        "correct": len(both_ok),
        "accuracy": _safe_div(len(both_ok), len(both)),
    }
    return out


def tag_metrics(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        for tag in r.get("tags") or ["untagged"]:
            by_tag[tag].append(r)
    return {
        tag: {
            "n": len(rs),
            "intent_accuracy": _safe_div(sum(r["score"]["intent_ok"] for r in rs), len(rs)),
            "pass_rate": _safe_div(sum(r["score"]["passed"] for r in rs), len(rs)),
        }
        for tag, rs in sorted(by_tag.items())
    }


def score_case(case: dict[str, Any], obs: Observation) -> dict[str, Any]:
    score = score_turn(case, obs)
    return {
        "id": case["id"],
        "text": case["text"],
        "tags": list(case.get("tags") or []),
        "verbatim_in_training": bool(case.get("verbatim_in_training", False)),
        "expected": {
            "intent": case["expected_intent"],
            "food_Item": case.get("expected_food_Item"),
            "number": expected_number(case.get("expected_number")),
            "allow_slot_prompt": bool(case.get("allow_slot_prompt", False)),
        },
        "observed": obs.to_dict(),
        "score": score,
        "failure": None if score["passed"] else describe_failure(case, obs, score),
    }


def score_conversation(conv: dict[str, Any], observations: list[Observation]) -> dict[str, Any]:
    turns = []
    first_fail = None
    for i, (turn, obs) in enumerate(
        # An aborted conversation has fewer observations than turns.
        zip(conv["turns"], observations, strict=False),
        start=1,
    ):
        score = score_turn(turn, obs)
        failure = None if score["passed"] else describe_failure(turn, obs, score)
        if failure and first_fail is None:
            first_fail = {"turn": i, "text": turn["text"], "failure": failure}
        turns.append(
            {
                "turn": i,
                "text": turn["text"],
                "expected_intent": turn["expected_intent"],
                "observed": obs.to_dict(),
                "score": score,
                "failure": failure,
            }
        )
    if len(observations) < len(conv["turns"]) and first_fail is None:
        i = len(observations) + 1
        first_fail = {"turn": i, "text": conv["turns"][i - 1]["text"], "failure": "not run"}
    return {
        "id": conv["id"],
        "description": conv.get("description", ""),
        "passed": first_fail is None,
        "turns_passed": sum(t["score"]["passed"] for t in turns),
        "turns_total": len(conv["turns"]),
        "first_failure": first_fail,
        "turns": turns,
    }


def summarize(
    case_records: list[dict[str, Any]], conv_records: list[dict[str, Any]]
) -> dict[str, Any]:
    pairs = [(r["expected"]["intent"], r["observed"]["intent"]) for r in case_records]
    cls = classification_metrics(pairs)
    turn_latencies = [r["observed"]["latency_ms"] for r in case_records] + [
        t["observed"]["latency_ms"] for c in conv_records for t in c["turns"]
    ]
    conv_turns = [t for c in conv_records for t in c["turns"]]
    n_cases = len(case_records)
    unseen = [r for r in case_records if not r.get("verbatim_in_training")]
    unseen_cls = classification_metrics(
        (r["expected"]["intent"], r["observed"]["intent"]) for r in unseen
    )
    return {
        "cases": n_cases,
        "intent_accuracy": cls["accuracy"],
        "macro_f1": cls["macro_f1"],
        "case_pass_rate": _safe_div(sum(r["score"]["passed"] for r in case_records), n_cases),
        # The same headline metrics without the cases whose text is also a
        # training phrase (memorised, so they say little about generalisation).
        "excluding_verbatim": {
            "cases": len(unseen),
            "excluded": n_cases - len(unseen),
            "intent_accuracy": unseen_cls["accuracy"],
            "macro_f1": unseen_cls["macro_f1"],
            "case_pass_rate": _safe_div(sum(r["score"]["passed"] for r in unseen), len(unseen)),
        },
        "unexpected_slot_prompts": sum(not r["score"]["prompt_ok"] for r in case_records),
        "errors": sum(1 for r in case_records if r["observed"].get("error"))
        + sum(1 for t in conv_turns if t["observed"].get("error")),
        "per_intent": cls["per_intent"],
        "labels": cls["labels"],
        "confusion": cls["confusion"],
        "slots": slot_metrics(case_records),
        "tags": tag_metrics(case_records),
        "latency": latency_stats(turn_latencies),
        "conversations": {
            "n": len(conv_records),
            "passed": sum(c["passed"] for c in conv_records),
            "pass_rate": _safe_div(sum(c["passed"] for c in conv_records), len(conv_records)),
            "turn_pass_rate": _safe_div(
                sum(t["score"]["passed"] for t in conv_turns), len(conv_turns)
            ),
        },
    }


# ------------------------------------------------------------------- gating


def compare_to_baseline(
    current: dict[str, Any], baseline: dict[str, Any], max_drop: float
) -> tuple[bool, list[str]]:
    """Gate: intent accuracy and conversation pass rate may not drop > max_drop.

    `current` / `baseline` are whole results.json objects. Per-intent F1 drops
    larger than max_drop are reported as warnings only (small supports make
    them noisy).
    """
    msgs: list[str] = []
    ok = True
    cur_meta, base_meta = current.get("meta", {}), baseline.get("meta", {})
    if cur_meta.get("golden_fingerprint") != base_meta.get("golden_fingerprint"):
        msgs.append(
            "WARNING: golden set differs from the baseline's "
            f"({base_meta.get('golden_fingerprint')} -> {cur_meta.get('golden_fingerprint')}); "
            "the comparison is not like-for-like."
        )
    cur, base = current["summary"], baseline["summary"]
    for name, cur_v, base_v in (
        ("intent_accuracy", cur.get("intent_accuracy"), base.get("intent_accuracy")),
        (
            "conversation_pass_rate",
            cur.get("conversations", {}).get("pass_rate"),
            base.get("conversations", {}).get("pass_rate"),
        ),
    ):
        if cur_v is None or base_v is None:
            continue
        drop = base_v - cur_v
        line = f"{name}: baseline {base_v:.3f} -> current {cur_v:.3f} (drop {drop:+.3f}, max {max_drop:.3f})"
        if drop > max_drop + 1e-12:
            ok = False
            msgs.append("REGRESSION " + line)
        else:
            msgs.append("ok " + line)
    for intent, bm in (base.get("per_intent") or {}).items():
        cm = (cur.get("per_intent") or {}).get(intent)
        if not cm or bm.get("f1") is None or cm.get("f1") is None:
            continue
        if bm["f1"] - cm["f1"] > max_drop + 1e-12:
            msgs.append(f"WARNING {intent} F1 {bm['f1']:.3f} -> {cm['f1']:.3f}")
    return ok, msgs


# ------------------------------------------------------------------- report


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _f(v: float | None, digits: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def _ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f} ms"


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _short(label: str) -> str:
    return {
        "Default Welcome Intent": "welcome",
        "Default Fallback Intent": "fallback",
        "track.order - more": "track.more",
    }.get(label, label)


def render_report(results: dict[str, Any]) -> str:
    meta, s = results["meta"], results["summary"]
    cases, convs = results.get("cases", []), results.get("conversations", [])
    out: list[str] = []
    w = out.append
    w(f"# NLU eval report: {meta.get('run_name', '')}")
    w("")
    w(
        f"Agent `{meta.get('project')}` (Dialogflow ES, language `{meta.get('language')}`, "
        f"{'environment `' + meta['environment'] + '`' if meta.get('environment') else 'draft'}). "
        f"Run at {meta.get('started_at')} UTC, {meta.get('api_calls')} API calls, "
        f"concurrency {meta.get('concurrency')}. Golden set fingerprint `{meta.get('golden_fingerprint')}`."
    )
    if meta.get("rescored_from"):
        w("")
        w(
            f"Re-scored offline from stored observations (`{meta['rescored_from']}`); latency is from that run."
        )
    w("")
    w("## Headline")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Single-turn cases | {s['cases']} |")
    w(f"| Intent accuracy | {_pct(s['intent_accuracy'])} |")
    w(f"| Macro F1 (intents) | {_f(s['macro_f1'])} |")
    w(f"| Case pass rate (intent + slots + prompt) | {_pct(s['case_pass_rate'])} |")
    ex = s.get("excluding_verbatim") or {}
    if ex.get("excluded"):
        w(
            f"| Without the {ex['excluded']} cases verbatim in training ({ex['cases']} cases): "
            f"intent accuracy / macro F1 / case pass rate | {_pct(ex['intent_accuracy'])} / "
            f"{_f(ex['macro_f1'])} / {_pct(ex['case_pass_rate'])} |"
        )
    w(
        f"| food_Item accuracy | {_pct(s['slots']['food_Item']['accuracy'])} ({s['slots']['food_Item']['correct']}/{s['slots']['food_Item']['checked']}) |"
    )
    w(
        f"| number accuracy | {_pct(s['slots']['number']['accuracy'])} ({s['slots']['number']['correct']}/{s['slots']['number']['checked']}) |"
    )
    w(
        f"| Conversation pass rate | {_pct(s['conversations']['pass_rate'])} ({s['conversations']['passed']}/{s['conversations']['n']}) |"
    )
    w(f"| Conversation turn pass rate | {_pct(s['conversations']['turn_pass_rate'])} |")
    w(f"| Unexpected slot prompts | {s['unexpected_slot_prompts']} |")
    w(f"| API errors (after retries) | {s['errors']} |")
    lat = s["latency"]
    w(
        f"| Latency p50 / p95 / max | {_ms(lat['p50_ms'])} / {_ms(lat['p95_ms'])} / {_ms(lat['max_ms'])} (n={lat['count']}) |"
    )
    w("")

    w("## Per-intent precision / recall / F1")
    w("")
    w("| Intent | Support | Precision | Recall | F1 |")
    w("|---|---:|---:|---:|---:|")
    for label in s["labels"]:
        m = s["per_intent"][label]
        w(
            f"| {label} | {m['support']} | {_f(m['precision'])} | {_f(m['recall'])} | {_f(m['f1'])} |"
        )
    w("")

    w("## Confusion matrix")
    w("")
    w("Rows = expected, columns = predicted.")
    w("")
    labels = s["labels"]
    w("| expected \\ predicted | " + " | ".join(_short(label) for label in labels) + " |")
    w("|---|" + "---:|" * len(labels))
    for exp in labels:
        row = s["confusion"].get(exp, {})
        if not any(row.values()):
            continue
        cells = []
        for pred in labels:
            n = row.get(pred, 0)
            cells.append(f"**{n}**" if exp == pred and n else (str(n) if n else "."))
        w(f"| {_short(exp)} | " + " | ".join(cells) + " |")
    w("")

    w("## Slot accuracy")
    w("")
    w(
        "`accuracy` is the extraction match on every case that labels the slot, regardless of the intent "
        "(a slot can be right under the wrong intent). `given intent` only counts cases where the intent "
        "was right, i.e. what the server would actually act on."
    )
    w("")
    w("| Slot | Checked | Accuracy | Checked (intent right) | Accuracy given intent |")
    w("|---|---:|---:|---:|---:|")
    for slot in ("food_Item", "number"):
        m = s["slots"][slot]
        w(
            f"| {slot} | {m['checked']} | {_pct(m['accuracy'])} | {m['checked_given_intent']} | {_pct(m['accuracy_given_intent'])} |"
        )
    a = s["slots"]["all_slots"]
    w(f"| all labelled slots | {a['checked']} | {_pct(a['accuracy'])} | | |")
    w("")

    w("## By tag")
    w("")
    w("| Tag | Cases | Intent accuracy | Pass rate |")
    w("|---|---:|---:|---:|")
    for tag, m in s["tags"].items():
        w(f"| {tag} | {m['n']} | {_pct(m['intent_accuracy'])} | {_pct(m['pass_rate'])} |")
    w("")

    w("## Conversations")
    w("")
    w("| Conversation | Result | Turns passed | First failing turn |")
    w("|---|---|---:|---|")
    for c in convs:
        ff = c["first_failure"]
        ff_txt = "" if not ff else f'{ff["turn"]}: "{_md(ff["text"])}" ({_md(ff["failure"])})'
        w(
            f"| {c['id']} | {'PASS' if c['passed'] else 'FAIL'} | {c['turns_passed']}/{c['turns_total']} | {ff_txt} |"
        )
    w("")

    failures = [r for r in cases if not r["score"]["passed"]]
    w(f"## Failures ({len(failures)})")
    w("")
    if failures:
        w("| Case | Text | Tags | What went wrong | Reply (truncated) |")
        w("|---|---|---|---|---|")
        for r in failures:
            reply = (r["observed"].get("reply") or "")[:70]
            w(
                f"| {r['id']} | {_md(r['text'])} | {', '.join(r['tags'])} | {_md(r['failure'])} | {_md(reply)} |"
            )
    else:
        w("None.")
    w("")
    if meta.get("notes"):
        w("## Notes")
        w("")
        for note in meta["notes"]:
            w(f"- {note}")
        w("")
    return "\n".join(out)
