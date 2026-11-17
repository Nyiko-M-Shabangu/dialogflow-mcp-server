"""Unit tests for the NLU eval harness (evals/). No network, no GCP.

A fake Dialogflow client stands in for SessionsClient; it returns canned
query results keyed by utterance (and by session state for multi-turn).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"
sys.path.insert(0, str(EVALS_DIR))

import leakage  # noqa: E402
import nlu_metrics as m  # noqa: E402
import run_nlu  # noqa: E402

# ------------------------------------------------------------------ helpers


def qr(intent, params=None, allreq=True, reply="ok", conf=0.9):
    """A query result shaped like the dict run_nlu builds from the proto."""
    return {
        "intent": {"displayName": intent},
        "intentDetectionConfidence": conf,
        "parameters": params or {},
        "allRequiredParamsPresent": allreq,
        "fulfillmentText": reply,
    }


class FakeClient:
    """Canned responses by text; records every call."""

    def __init__(self, responses, errors=None):
        self.responses = responses
        self.errors = errors or {}
        self.calls = []

    def detect(self, session_id, text, input_contexts=None):
        self.calls.append((session_id, text, tuple(input_contexts or ())))
        if text in self.errors:
            return m.error_observation(self.errors[text])
        return m.observation_from_query_result(self.responses[text], latency_ms=100.0)


def case(**kw):
    base = {"id": "c1", "text": "x", "expected_intent": "order.add", "tags": []}
    base.update(kw)
    return base


# ---------------------------------------------------------------- parsing


def test_normalize_number_floats_strings_scalars_and_blanks():
    assert m.normalize_number([2.0, 1.0]) == [2, 1]
    assert m.normalize_number(1005.0) == [1005]
    assert m.normalize_number("") == []
    assert m.normalize_number(None) == []
    assert m.normalize_number(["3", "", 2.5]) == [3, 2.5]
    assert m.normalize_number([True, "abc"]) == []
    assert all(isinstance(n, int) for n in m.normalize_number([2.0, 7.0]))


def test_normalize_food_list_string_and_blank():
    assert m.normalize_food(["Kota", "Bunny Chow"]) == ["Kota", "Bunny Chow"]
    assert m.normalize_food("Kota") == ["Kota"]
    assert m.normalize_food("") == []
    assert m.normalize_food(["", " Kota "]) == ["Kota"]


def test_observation_from_query_result_matches_probe_shapes():
    # order.add: number shorter than food_Item ("a kota and 2 bunny chows")
    obs = m.observation_from_query_result(
        qr("order.add", {"food_Item": ["Kota", "Bunny Chow"], "number": [2.0]}), 120.0
    )
    assert obs.intent == "order.add"
    assert obs.food_Item == ["Kota", "Bunny Chow"]
    assert obs.number == [2]
    assert obs.latency_ms == 120.0
    # track.order slot prompt: number is "" and allRequiredParamsPresent false
    obs = m.observation_from_query_result(qr("track.order", {"number": ""}, allreq=False))
    assert obs.number == [] and obs.all_required_params_present is False
    # proto3 drops false booleans; a missing key means False
    raw = qr("track.order")
    del raw["allRequiredParamsPresent"]
    assert m.observation_from_query_result(raw).all_required_params_present is False
    # no intent at all
    assert m.observation_from_query_result({}).intent == m.NO_INTENT


def test_observation_roundtrip():
    obs = m.Observation(intent="order.add", food_Item=["Kota"], number=[1], latency_ms=5.0)
    assert m.Observation.from_dict(obs.to_dict() | {"extra": 1}) == obs


# ---------------------------------------------------------------- scoring


def test_score_turn_all_correct_order_insensitive_food():
    c = case(expected_food_Item=["Bunny Chow", "Kota"], expected_number=[1, 2])
    obs = m.Observation("order.add", food_Item=["Kota", "Bunny Chow"], number=[1, 2])
    s = m.score_turn(c, obs)
    assert s["intent_ok"] and s["food_ok"] and s["number_ok"] and s["passed"]


def test_score_turn_number_is_order_sensitive_and_length_sensitive():
    c = case(expected_food_Item=["Kota", "Bunny Chow"], expected_number=[1, 2])
    s = m.score_turn(c, m.Observation("order.add", food_Item=["Kota", "Bunny Chow"], number=[2]))
    assert s["food_ok"] is True and s["number_ok"] is False and not s["passed"]
    s = m.score_turn(c, m.Observation("order.add", food_Item=["Kota", "Bunny Chow"], number=[2, 1]))
    assert s["number_ok"] is False


def test_score_turn_scalar_track_id():
    c = case(expected_intent="track.order", expected_number=1005)
    assert m.score_turn(c, m.Observation("track.order", number=[1005]))["passed"]
    assert not m.score_turn(c, m.Observation("track.order", number=[105]))["passed"]


def test_score_turn_empty_food_label_means_must_not_extract():
    c = case(expected_intent="Default Fallback Intent", expected_food_Item=[])
    assert m.score_turn(c, m.Observation("Default Fallback Intent"))["passed"]
    s = m.score_turn(c, m.Observation("Default Fallback Intent", food_Item=["Kota"]))
    assert s["intent_ok"] and s["food_ok"] is False and not s["passed"]


def test_score_turn_unlabelled_slots_are_not_checked():
    s = m.score_turn(
        case(expected_intent="new.order"), m.Observation("new.order", food_Item=["Kota"])
    )
    assert s["food_ok"] is None and s["number_ok"] is None and s["passed"]


def test_score_turn_slot_prompt_rules():
    prompted = m.Observation("track.order", all_required_params_present=False)
    # allowed prompt passes, and a number label is skipped while prompting
    allowed = case(expected_intent="track.order", allow_slot_prompt=True, expected_number=1005)
    s = m.score_turn(allowed, prompted)
    assert s["prompted"] and s["prompt_ok"] and s["number_ok"] is None and s["passed"]
    # an unexpected prompt fails even when the intent is right
    s = m.score_turn(case(expected_intent="track.order"), prompted)
    assert s["intent_ok"] and not s["prompt_ok"] and not s["passed"]


def test_score_turn_reply_and_error():
    c = case(expected_intent="new.order", expect_reply=True)
    assert m.score_turn(c, m.Observation("new.order", reply="hi"))["passed"]
    assert m.score_turn(c, m.Observation("new.order", reply="  "))["reply_ok"] is False
    err = m.error_observation("ResourceExhausted: 429")
    s = m.score_turn(c, err)
    assert not s["passed"]
    assert "error" in m.describe_failure(c, err, s)


def test_describe_failure_lists_each_problem():
    c = case(expected_food_Item=["Kota"], expected_number=[2])
    obs = m.Observation("Default Fallback Intent", food_Item=[], number=[])
    msg = m.describe_failure(c, obs, m.score_turn(c, obs))
    assert "intent order.add -> Default Fallback Intent" in msg
    assert "food_Item" in msg and "number [2] -> []" in msg


# ---------------------------------------------------------------- metrics


def test_classification_metrics_hand_computed():
    pairs = [
        ("a", "a"),
        ("a", "a"),
        ("a", "b"),  # a: tp2 fn1
        ("b", "b"),
        ("b", "a"),  # b: tp1 fn1, fp1 (from a)
        ("c", "<error>"),  # c: never predicted
    ]
    res = m.classification_metrics(pairs)
    assert res["accuracy"] == pytest.approx(3 / 6)
    a, b, c = (res["per_intent"][k] for k in ("a", "b", "c"))
    assert (a["precision"], a["recall"]) == (pytest.approx(2 / 3), pytest.approx(2 / 3))
    assert (b["precision"], b["recall"]) == (pytest.approx(1 / 2), pytest.approx(1 / 2))
    assert c["support"] == 1 and c["precision"] is None and c["recall"] == 0.0 and c["f1"] is None
    # predicted-only label: support 0, recall undefined, counted as a FP
    err = res["per_intent"]["<error>"]
    assert err["support"] == 0 and err["recall"] is None and err["fp"] == 1
    # macro F1 over supported labels, undefined F1 counted as 0
    assert res["macro_f1"] == pytest.approx((2 / 3 + 1 / 2 + 0) / 3)
    assert res["confusion"]["a"] == {"a": 2, "b": 1}
    assert res["confusion"]["c"] == {"<error>": 1}
    assert "<error>" in res["labels"]


def test_classification_metrics_empty():
    res = m.classification_metrics([])
    assert res["accuracy"] is None and res["macro_f1"] is None and res["per_intent"] == {}


def test_percentile_and_latency_stats():
    assert m.percentile([], 50) is None
    assert m.percentile([7], 95) == 7
    vals = list(range(1, 101))  # 1..100
    assert m.percentile(vals, 50) == pytest.approx(50.5)
    assert m.percentile(vals, 95) == pytest.approx(95.05)
    st = m.latency_stats([10, None, 30, 20])
    assert st["count"] == 3 and st["p50_ms"] == 20 and st["max_ms"] == 30


def _rec(intent_ok, food_ok=None, number_ok=None, passed=None, tags=()):
    return {
        "tags": list(tags),
        "score": {
            "intent_ok": intent_ok,
            "food_ok": food_ok,
            "number_ok": number_ok,
            "passed": intent_ok if passed is None else passed,
        },
    }


def test_slot_metrics_two_views():
    recs = [
        _rec(True, food_ok=True, number_ok=True),
        _rec(True, food_ok=False, number_ok=True, passed=False),
        _rec(False, food_ok=False, number_ok=False),  # wrong intent
        _rec(True),  # no slot labels
    ]
    s = m.slot_metrics(recs)
    assert s["food_Item"]["checked"] == 3
    assert s["food_Item"]["accuracy"] == pytest.approx(1 / 3)
    assert s["food_Item"]["checked_given_intent"] == 2
    assert s["food_Item"]["accuracy_given_intent"] == pytest.approx(1 / 2)
    assert s["number"]["accuracy"] == pytest.approx(2 / 3)
    assert s["all_slots"] == {"checked": 3, "correct": 1, "accuracy": pytest.approx(1 / 3)}


def test_slot_accuracy_is_independent_of_intent_with_real_scores():
    # "a kota" -> order_remove with food [Kota]: the slot is right, the intent is not
    c = case(id="a", expected_food_Item=["Kota"], expected_number=[1])
    rec = m.score_case(c, m.Observation("order_remove", food_Item=["Kota"], number=[]))
    s = m.slot_metrics([rec])
    assert s["food_Item"]["accuracy"] == 1.0
    assert s["food_Item"]["checked_given_intent"] == 0
    assert s["food_Item"]["accuracy_given_intent"] is None
    assert s["number"]["accuracy"] == 0.0


def test_tag_metrics():
    recs = [_rec(True, tags=["slang"]), _rec(False, tags=["slang", "typo"]), _rec(True)]
    t = m.tag_metrics(recs)
    assert t["slang"]["n"] == 2 and t["slang"]["intent_accuracy"] == 0.5
    assert t["typo"]["pass_rate"] == 0.0
    assert t["untagged"]["n"] == 1


# ----------------------------------------------------------- conversations


def test_score_conversation_first_failure_and_not_run():
    conv = {
        "id": "conv",
        "turns": [
            {"text": "new order", "expected_intent": "new.order"},
            {"text": "a kota", "expected_intent": "order.add", "expected_number": [1]},
            {"text": "done", "expected_intent": "order.complete"},
        ],
    }
    obs = [
        m.Observation("new.order"),
        m.Observation("order.add", food_Item=["Kota"], number=[]),
        m.Observation("order.complete"),
    ]
    rec = m.score_conversation(conv, obs)
    assert not rec["passed"] and rec["turns_passed"] == 2
    assert rec["first_failure"]["turn"] == 2 and "number" in rec["first_failure"]["failure"]

    rec = m.score_conversation(conv, obs[:1])  # aborted after turn 1
    assert not rec["passed"] and rec["first_failure"] == {
        "turn": 2,
        "text": "a kota",
        "failure": "not run",
    }


# --------------------------------------------------------- golden loading


def test_golden_files_are_valid_and_sized():
    cases = m.load_cases(run_nlu.DEFAULT_CASES)
    convs = m.load_conversations(run_nlu.DEFAULT_CONVERSATIONS)
    assert 150 <= len(cases) <= 250
    assert 10 <= len(convs) <= 20
    assert {c["expected_intent"] for c in cases} == set(m.INTENTS)
    # stays inside the free-tier budget
    assert run_nlu.planned_calls(cases, convs) <= 300


def test_verbatim_labels_match_the_training_phrases():
    # A case is labelled verbatim_in_training exactly when its normalised text
    # is a training phrase of the committed agent. Re-run after agent changes.
    cases = m.load_cases(run_nlu.DEFAULT_CASES)
    phrases = [p for _, p in leakage.training_phrases()]
    labelled = {c["id"] for c in cases if c.get("verbatim_in_training")}
    assert labelled == leakage.verbatim_case_ids(cases, phrases)
    assert labelled  # the dev set does overlap; the report must say so


def test_heldout_set_is_valid_and_has_no_near_match():
    cases = m.load_cases(leakage.HELDOUT)
    convs = m.load_conversations(leakage.HELDOUT)
    assert 40 <= len(cases) <= 80 and 3 <= len(convs) <= 8
    # The held-out set predates menu.ask (added in 1.1.0) and is never edited.
    assert {c["expected_intent"] for c in cases} == set(m.INTENTS) - {"menu.ask"}
    assert not any(c.get("verbatim_in_training") for c in cases)
    phrases = [p for _, p in leakage.training_phrases()]
    dev = leakage.dev_texts(
        m.load_cases(run_nlu.DEFAULT_CASES), m.load_conversations(run_nlu.DEFAULT_CONVERSATIONS)
    )
    hits = leakage.near_matches(leakage.heldout_texts(cases, convs), phrases + dev)
    assert leakage.unexpected_heldout_hits(hits) == [], (
        "held-out utterances must not resemble training phrases or the dev set"
    )
    # Every documented exception is still real (drop it once it no longer applies).
    assert {(h[0], h[1]) for h in hits} == set(leakage.HELDOUT_KNOWN_OVERLAP)
    assert run_nlu.planned_calls(cases, convs) <= 100


def test_normalize_and_similarity():
    assert leakage.normalize("  Pap & Chakalaka!! ") == "pap and chakalaka"
    assert leakage.normalize("That's ALL") == "that s all"
    assert leakage.similarity("hello", "Hello!") == 1.0
    assert leakage.best_match("new ordr", ["new order", "track order"])[1] == "new order"


def test_summary_excludes_verbatim_cases():
    def rec(cid, intent, got, verbatim):
        case = {"id": cid, "text": cid, "expected_intent": intent, "verbatim_in_training": verbatim}
        return m.score_case(case, m.Observation(intent=got, all_required_params_present=True))

    recs = [
        rec("a", "new.order", "new.order", True),
        rec("b", "new.order", "new.order", False),
        rec("c", "new.order", "track.order", False),
    ]
    s = m.summarize(recs, [])
    assert s["intent_accuracy"] == pytest.approx(2 / 3)
    ex = s["excluding_verbatim"]
    assert (ex["cases"], ex["excluded"]) == (2, 1)
    assert ex["intent_accuracy"] == pytest.approx(0.5)
    report = m.render_report({"meta": {}, "summary": s, "cases": recs, "conversations": []})
    assert "Without the 1 cases verbatim in training (2 cases)" in report


def test_validate_rejects_non_bool_verbatim_flag():
    with pytest.raises(m.GoldenSetError, match="verbatim_in_training"):
        m.validate_cases(
            [
                {
                    "id": "x",
                    "text": "hi",
                    "expected_intent": "new.order",
                    "verbatim_in_training": "yes",
                }
            ]
        )


@pytest.mark.parametrize(
    "bad, msg",
    [
        ({"id": "x", "text": "hi", "expected_intent": "nope"}, "expected_intent"),
        (
            {
                "id": "x",
                "text": "hi",
                "expected_intent": "order.add",
                "expected_food_Item": ["Pizza"],
            },
            "menu",
        ),
        ({"id": "x", "text": "", "expected_intent": "order.add"}, "text"),
        ({"id": "x", "text": "hi", "expected_intent": "order.add", "typo_key": 1}, "unknown"),
        (
            {"id": "x", "text": "hi", "expected_intent": "order.add", "expected_number": "two"},
            "numeric",
        ),
    ],
)
def test_validate_cases_rejects_bad_labels(bad, msg):
    with pytest.raises(m.GoldenSetError, match=msg):
        m.validate_cases([bad])


def test_validate_cases_rejects_duplicate_ids():
    c = {"id": "x", "text": "hi", "expected_intent": "new.order"}
    with pytest.raises(m.GoldenSetError, match="duplicate"):
        m.validate_cases([c, dict(c)])


def test_golden_fingerprint_is_stable_and_sensitive():
    a = {"cases": [{"id": "1", "text": "hi"}]}
    assert m.golden_fingerprint(a) == m.golden_fingerprint(json.loads(json.dumps(a)))
    assert m.golden_fingerprint(a) != m.golden_fingerprint({"cases": [{"id": "1", "text": "hey"}]})


# ------------------------------------------------------------------ retry


class Quota(Exception):
    code = 429


class BadRequest(Exception):
    code = 400


def test_call_with_retry_backs_off_on_429_then_succeeds():
    sleeps, calls = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise Quota("RESOURCE_EXHAUSTED")
        return "ok"

    assert run_nlu.call_with_retry(fn, attempts=5, base_delay=1.0, sleep=sleeps.append) == "ok"
    assert len(calls) == 3 and len(sleeps) == 2
    assert sleeps[1] > sleeps[0] * 0.9  # exponential-ish (jitter 0.5-1.0x)


def test_call_with_retry_gives_up_and_does_not_retry_client_errors():
    sleeps = []
    with pytest.raises(Quota):
        run_nlu.call_with_retry(
            lambda: (_ for _ in ()).throw(Quota()), attempts=3, sleep=sleeps.append
        )
    assert len(sleeps) == 2
    sleeps.clear()
    with pytest.raises(BadRequest):
        run_nlu.call_with_retry(lambda: (_ for _ in ()).throw(BadRequest()), sleep=sleeps.append)
    assert sleeps == []


def test_rate_limiter_spaces_calls():
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    lim = run_nlu.RateLimiter(120, clock=lambda: now[0], sleep=sleep)  # 0.5 s apart
    for _ in range(3):
        lim.acquire()
    assert sleeps == [pytest.approx(0.5), pytest.approx(0.5)]
    now[0] += 10  # idle: no wait
    lim.acquire()
    assert len(sleeps) == 2
    run_nlu.RateLimiter(0).acquire()  # disabled


def test_is_retryable():
    assert run_nlu.is_retryable(Quota())
    assert run_nlu.is_retryable(RuntimeError("503 UNAVAILABLE"))
    assert not run_nlu.is_retryable(BadRequest("invalid argument"))


# ----------------------------------------------------- runner, fake client


CASES = [
    {
        "id": "a1",
        "text": "a kota",
        "expected_intent": "order.add",
        "expected_food_Item": ["Kota"],
        "expected_number": [1],
        "input_contexts": ["ongoing-order"],
        "tags": ["a/an-quantity"],
    },
    {
        "id": "a2",
        "text": "2 bunny chows",
        "expected_intent": "order.add",
        "expected_food_Item": ["Bunny Chow"],
        "expected_number": [2],
        "context": ["new order"],
        "tags": [],
    },
    {
        "id": "f1",
        "text": "I want a pizza",
        "expected_intent": "Default Fallback Intent",
        "expected_food_Item": [],
        "input_contexts": ["ongoing-order"],
        "tags": ["off-menu"],
    },
    {
        "id": "t1",
        "text": "track order",
        "expected_intent": "track.order",
        "allow_slot_prompt": True,
        "tags": [],
    },
    {
        "id": "w1",
        "text": "hello",
        "expected_intent": "Default Welcome Intent",
        "tags": ["chit-chat"],
    },
]
CONVS = [
    {
        "id": "conv-track",
        "turns": [
            {
                "text": "track order",
                "expected_intent": "track.order",
                "allow_slot_prompt": True,
                "expect_reply": True,
            },
            {"text": "1005", "expected_intent": "track.order", "expected_number": 1005},
        ],
    },
]
RESPONSES = {
    "a kota": qr(
        "order.add",
        {"food_Item": ["Kota"], "number": []},
        allreq=False,
        reply="Please specify how much you want.",
    ),
    "2 bunny chows": qr("order.add", {"food_Item": ["Bunny Chow"], "number": [2.0]}),
    "new order": qr("new.order"),
    "I want a pizza": qr("Default Fallback Intent"),
    "track order": qr("track.order", {"number": ""}, allreq=False, reply="What is your order id?"),
    "1005": qr("track.order", {"number": 1005.0}, reply="Checking order #1005."),
    "hello": qr("Default Fallback Intent", reply="I didn't understand."),
}


def test_run_all_with_fake_client_end_to_end(tmp_path):
    fake = FakeClient(RESPONSES)
    case_recs, conv_recs, calls = run_nlu.run_all(fake, CASES, CONVS, concurrency=2, run_tag="t")
    assert calls == run_nlu.planned_calls(CASES, CONVS) == 8
    # context setup turn shares the session with its target; ids are unique and short
    sess = {}
    for sid, text, _ in fake.calls:
        sess.setdefault(sid, []).append(text)
        assert sid.startswith("eval-") and len(sid) <= 36
    assert ["new order", "2 bunny chows"] in sess.values()
    assert ["track order", "1005"] in sess.values()
    assert ("ongoing-order",) in {ctx for _, _, ctx in fake.calls}

    by_id = {r["id"]: r for r in case_recs}
    assert by_id["a1"]["score"]["number_ok"] is False  # "a" not extracted as 1
    assert not by_id["a1"]["score"]["prompt_ok"]
    assert by_id["a2"]["score"]["passed"]
    assert by_id["f1"]["score"]["passed"]
    assert by_id["t1"]["score"]["passed"]
    assert not by_id["w1"]["score"]["passed"]
    assert conv_recs[0]["passed"]

    s = m.summarize(case_recs, conv_recs)
    assert s["intent_accuracy"] == pytest.approx(4 / 5)
    assert s["case_pass_rate"] == pytest.approx(3 / 5)
    assert s["conversations"]["pass_rate"] == 1.0
    assert s["confusion"]["Default Welcome Intent"] == {"Default Fallback Intent": 1}
    assert s["latency"]["count"] == 7 and s["latency"]["p50_ms"] == 100.0
    assert s["unexpected_slot_prompts"] == 1

    report = m.render_report(
        run_nlu.build_results(
            meta={"run_name": "t", "project": "p", "language": "en"},
            case_recs=case_recs,
            conv_recs=conv_recs,
        )
    )
    assert "## Confusion matrix" in report and "| a1 |" in report and "conv-track | PASS" in report


def test_conversation_stops_after_api_error():
    fake = FakeClient(RESPONSES, errors={"track order": "ResourceExhausted: quota"})
    _, conv_recs, calls = run_nlu.run_all(fake, [], CONVS, concurrency=1)
    assert calls == 1
    rec = conv_recs[0]
    assert not rec["passed"] and rec["first_failure"]["turn"] == 1
    assert m.summarize([], conv_recs)["errors"] == 1


def _write_golden(tmp_path):
    import yaml

    cases_p = tmp_path / "cases.yaml"
    convs_p = tmp_path / "convs.yaml"
    cases_p.write_text(yaml.safe_dump({"cases": CASES}), encoding="utf-8")
    convs_p.write_text(yaml.safe_dump({"conversations": CONVS}), encoding="utf-8")
    return cases_p, convs_p


def test_main_writes_report_and_gates(tmp_path, capsys):
    cases_p, convs_p = _write_golden(tmp_path)
    out = tmp_path / "run1"
    base = ["--cases", str(cases_p), "--conversations", str(convs_p)]
    rc = run_nlu.main([*base, "--out", str(out)], client=FakeClient(RESPONSES))
    assert rc == 0
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["meta"]["api_calls"] == 8
    assert results["summary"]["intent_accuracy"] == pytest.approx(0.8)
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# NLU eval report: run1")

    # --min-accuracy gate
    rc = run_nlu.main(
        [*base, "--out", str(tmp_path / "run2"), "--min-accuracy", "0.9"],
        client=FakeClient(RESPONSES),
    )
    assert rc == 1

    # baseline regression gate: "2 bunny chows" now falls back
    worse = dict(RESPONSES, **{"2 bunny chows": qr("Default Fallback Intent")})
    rc = run_nlu.main(
        [
            *base,
            "--out",
            str(tmp_path / "run3"),
            "--baseline",
            str(out / "results.json"),
            "--max-drop",
            "0.02",
        ],
        client=FakeClient(worse),
    )
    assert rc == 2
    gate = json.loads((tmp_path / "run3" / "results.json").read_text(encoding="utf-8"))["gate"]
    assert any(msg.startswith("REGRESSION intent_accuracy") for msg in gate["messages"])

    # same agent vs baseline: passes
    rc = run_nlu.main(
        [*base, "--out", str(tmp_path / "run4"), "--baseline", str(out / "results.json")],
        client=FakeClient(RESPONSES),
    )
    assert rc == 0


def test_main_rescore_is_offline_and_max_calls_guard(tmp_path):
    cases_p, convs_p = _write_golden(tmp_path)
    base = ["--cases", str(cases_p), "--conversations", str(convs_p)]
    out = tmp_path / "run1"
    assert run_nlu.main([*base, "--out", str(out)], client=FakeClient(RESPONSES)) == 0

    class Exploding:
        def detect(self, *a, **k):
            raise AssertionError("rescore must not call the API")

    out2 = tmp_path / "rescored"
    rc = run_nlu.main(
        [*base, "--out", str(out2), "--rescore", str(out / "results.json")], client=Exploding()
    )
    assert rc == 0
    r1 = json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]
    r2 = json.loads((out2 / "results.json").read_text(encoding="utf-8"))["summary"]
    assert r1["intent_accuracy"] == r2["intent_accuracy"]
    assert r1["conversations"] == r2["conversations"]

    assert (
        run_nlu.main([*base, "--out", str(tmp_path / "x"), "--max-calls", "3"], client=Exploding())
        == 3
    )


def test_compare_to_baseline_warns_on_different_golden_set():
    cur = {
        "meta": {"golden_fingerprint": "a"},
        "summary": {"intent_accuracy": 0.9, "conversations": {"pass_rate": 0.5}},
    }
    base = {
        "meta": {"golden_fingerprint": "b"},
        "summary": {"intent_accuracy": 0.9, "conversations": {"pass_rate": 0.5}},
    }
    ok, msgs = m.compare_to_baseline(cur, base, 0.02)
    assert ok and msgs[0].startswith("WARNING: golden set differs")


def test_dialogflow_client_session_paths_without_network(monkeypatch):
    client = run_nlu.DialogflowESClient.__new__(run_nlu.DialogflowESClient)
    client.project, client.location, client.environment = "p", "global", None
    assert client._session("s") == "projects/p/agent/sessions/s"
    client.environment = "staging"
    assert client._session("s") == "projects/p/agent/environments/staging/users/-/sessions/s"
    client.location, client.environment = "europe-west2", None
    assert client._session("s") == "projects/p/locations/europe-west2/agent/sessions/s"


# ------------------------------------------------------------ order_line variant


def test_order_line_derives_flat_slots_aligned():
    obs = m.observation_from_query_result(
        {
            "intent": {"displayName": "order.add"},
            "allRequiredParamsPresent": True,
            "parameters": {
                "order_line": [
                    {"food_Item": "Kota", "number": "1"},
                    {"number": 2.0, "food_Item": "Bunny Chow"},
                    {"food_Item": "Milk Tart"},
                ]
            },
        }
    )
    assert obs.food_Item == ["Kota", "Bunny Chow", "Milk Tart"]
    assert obs.number == [1, 2, 1]
    assert obs.order_line == [
        {"food_Item": "Kota", "number": 1},
        {"food_Item": "Bunny Chow", "number": 2},
        {"food_Item": "Milk Tart", "number": None},
    ]


def test_flat_params_unchanged_without_order_line():
    obs = m.observation_from_query_result(
        {
            "intent": {"displayName": "order.add"},
            "parameters": {"food_Item": ["Kota"], "number": [2.0]},
        }
    )
    assert (obs.food_Item, obs.number, obs.order_line) == (["Kota"], [2], None)
    # old stored observations (no order_line key) still load
    assert m.Observation.from_dict({"intent": "order.add"}).order_line is None


def test_order_line_raw_pairs_scored_without_default():
    case = {
        "id": "x",
        "text": "a kota and 2 bunny chows",
        "expected_intent": "order.add",
        "expected_food_Item": ["Kota", "Bunny Chow"],
        "expected_number": [1, 2],
    }

    def obs(lines):
        return m.observation_from_query_result(
            {
                "intent": {"displayName": "order.add"},
                "allRequiredParamsPresent": True,
                "parameters": {"order_line": lines},
            }
        )

    good = m.score_case(
        case, obs([{"food_Item": "Kota", "number": "1"}, {"food_Item": "Bunny Chow", "number": 2}])
    )
    assert good["score"]["passed"] and good["score"]["order_line_ok"] is True
    # derived flat slots pass (missing -> 1) but the raw pair check does not
    nodefault = m.score_case(
        case, obs([{"food_Item": "Kota"}, {"food_Item": "Bunny Chow", "number": 2}])
    )
    assert nodefault["score"]["passed"] and nodefault["score"]["order_line_ok"] is False
    # swapped alignment fails both
    swapped = m.score_case(
        case, obs([{"food_Item": "Kota", "number": 2}, {"food_Item": "Bunny Chow", "number": 1}])
    )
    assert not swapped["score"]["passed"] and swapped["score"]["order_line_ok"] is False
    s = m.slot_metrics([good, nodefault, swapped])
    assert s["order_line_raw"] == {"checked": 3, "correct": 1, "accuracy": 1 / 3}
    # flat-only records produce no order_line row
    flat = m.score_case(
        case,
        m.observation_from_query_result(
            {
                "intent": {"displayName": "order.add"},
                "parameters": {"food_Item": ["Kota", "Bunny Chow"], "number": [1, 2]},
            }
        ),
    )
    assert flat["score"]["order_line_ok"] is None and "order_line_raw" not in m.slot_metrics([flat])
