"""NLU evaluation harness for the Dialogflow ES food-ordering agent.

Sends the golden utterances straight to Dialogflow (SessionsClient.detect_intent),
NOT through the MCP server, so no rows are written to the production database.

    uv run python evals/run_nlu.py                       # full run
    uv run python evals/run_nlu.py --limit 5             # smoke test
    uv run python evals/run_nlu.py --dry-run             # print the call plan only
    uv run python evals/run_nlu.py --rescore evals/reports/<run>/results.json
    uv run python evals/run_nlu.py --baseline evals/reports/baseline-2026-11-17/results.json \
        --max-drop 0.02 --min-accuracy 0.80              # CI gate

Auth: --gcloud (token of the gcloud CLI account), else --credentials-file KEY.json,
else GOOGLE_APPLICATION_CREDENTIALS_JSON
(key content), else Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS
pointing at a key file, gcloud ADC, ...). detect_intent only needs the
"Dialogflow API Client" role.

Exit codes: 0 ok, 1 intent accuracy below --min-accuracy, 2 regression vs
--baseline beyond --max-drop, 3 bad input / setup error.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

EVALS_DIR = Path(__file__).resolve().parent
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import nlu_metrics as m  # noqa: E402

DEFAULT_CASES = EVALS_DIR / "golden" / "nlu_single_turn.yaml"
DEFAULT_CONVERSATIONS = EVALS_DIR / "golden" / "conversations.yaml"
RETRYABLE_HTTP_CODES = {429, 500, 503, 504}
RETRYABLE_MARKERS = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "429")


class NLUClient(Protocol):
    def detect(
        self, session_id: str, text: str, input_contexts: list[str] | None = None
    ) -> m.Observation: ...


# ------------------------------------------------------------------- retry


def is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in RETRYABLE_HTTP_CODES:
        return True
    name = type(exc).__name__
    if name in {
        "ResourceExhausted",
        "TooManyRequests",
        "ServiceUnavailable",
        "DeadlineExceeded",
        "InternalServerError",
    }:
        return True
    text = str(exc)
    return any(marker in text for marker in RETRYABLE_MARKERS)


class RateLimiter:
    """Spaces calls at least 60/max_per_minute seconds apart, across threads.

    The Dialogflow Trial edition has a per-minute text-query quota; staying
    under it is cheaper than retrying 429s.
    """

    def __init__(
        self,
        max_per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 60.0 / max_per_minute if max_per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = self._clock()
            slot = max(now, self._next)
            self._next = slot + self.interval
        wait = slot - now
        if wait > 0:
            self._sleep(wait)


def call_with_retry(
    fn: Callable[[], Any],
    *,
    attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call fn, retrying quota / transient errors with exponential backoff + jitter.

    Defaults wait about 2+4+8+16+30 s (times 0.5-1.0 jitter) in total, enough to
    reach the next per-minute quota window.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            sleep(delay * (0.5 + random.random() / 2))
    raise AssertionError("unreachable")


# --------------------------------------------------------------- dialogflow


class DialogflowESClient:
    """Thin wrapper over dialogflow_v2.SessionsClient. Imports google lazily."""

    def __init__(
        self,
        project: str,
        *,
        language: str = "en",
        environment: str | None = None,
        location: str = "global",
        credentials_file: str | None = None,
        use_gcloud: bool = False,
        attempts: int = 6,
        max_per_minute: float = 100.0,
    ) -> None:
        from google.cloud import dialogflow_v2 as dialogflow
        from google.protobuf.json_format import MessageToDict

        self._df = dialogflow
        self._to_dict = MessageToDict
        self.project = project
        self.language = language
        self.environment = environment
        self.location = location
        self.attempts = attempts
        self.limiter = RateLimiter(max_per_minute)
        kwargs: dict[str, Any] = {}
        if use_gcloud:
            import shutil
            import subprocess

            from google.api_core.client_options import ClientOptions
            from google.oauth2.credentials import Credentials

            gcloud = shutil.which("gcloud") or "gcloud"
            token = (
                subprocess.run(
                    [gcloud, "auth", "print-access-token"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                .stdout.strip()
                .splitlines()[-1]
            )
            kwargs["credentials"] = Credentials(token)
            # user credentials need a quota project; bill the target project
            kwargs["client_options"] = ClientOptions(quota_project_id=project)
        elif credentials_file:
            from google.oauth2 import service_account

            kwargs["credentials"] = service_account.Credentials.from_service_account_file(
                credentials_file,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        else:
            from dialogflow_mcp.gcp_auth import client_kwargs

            kwargs.update(client_kwargs())
        if location != "global":
            from google.api_core.client_options import ClientOptions

            kwargs["client_options"] = ClientOptions(
                api_endpoint=f"{location}-dialogflow.googleapis.com",
                quota_project_id=project if use_gcloud else None,
            )
        self._client = dialogflow.SessionsClient(**kwargs)

    def _session(self, session_id: str) -> str:
        loc = "" if self.location == "global" else f"/locations/{self.location}"
        if self.environment:
            return (
                f"projects/{self.project}{loc}/agent/environments/{self.environment}"
                f"/users/-/sessions/{session_id}"
            )
        return f"projects/{self.project}{loc}/agent/sessions/{session_id}"

    def detect(
        self, session_id: str, text: str, input_contexts: list[str] | None = None
    ) -> m.Observation:
        session = self._session(session_id)
        request: dict[str, Any] = {
            "session": session,
            "query_input": {"text": {"text": text, "language_code": self.language}},
        }
        if input_contexts:
            request["query_params"] = {
                "contexts": [
                    {"name": f"{session}/contexts/{name}", "lifespan_count": 5}
                    for name in input_contexts
                ]
            }
        timing: dict[str, float] = {}

        def _call():
            self.limiter.acquire()  # retries count against the quota too
            t0 = time.perf_counter()
            resp = self._client.detect_intent(request=request, timeout=30)
            timing["ms"] = (time.perf_counter() - t0) * 1000
            return resp

        try:
            resp = call_with_retry(_call, attempts=self.attempts)
        except Exception as exc:
            return m.error_observation(f"{type(exc).__name__}: {str(exc)[:200]}")
        qr = resp.query_result
        raw = {
            "intent": {"displayName": qr.intent.display_name},
            "intentDetectionConfidence": qr.intent_detection_confidence,
            "parameters": self._to_dict(qr._pb).get("parameters", {}),
            "allRequiredParamsPresent": qr.all_required_params_present,
            "fulfillmentText": qr.fulfillment_text,
        }
        return m.observation_from_query_result(raw, latency_ms=timing.get("ms"))


# ------------------------------------------------------------------ running


def new_session_id(run_tag: str) -> str:
    # Dialogflow session ids must be <= 36 chars.
    return f"eval-{run_tag}-{uuid.uuid4().hex[:16]}"


class CallCounter:
    def __init__(self) -> None:
        self.n = 0
        self._lock = threading.Lock()

    def wrap(self, client: NLUClient) -> NLUClient:
        counter = self

        class _Counted:
            def detect(self, session_id, text, input_contexts=None):
                with counter._lock:
                    counter.n += 1
                return client.detect(session_id, text, input_contexts)

        return _Counted()


def run_case(client: NLUClient, case: dict[str, Any], run_tag: str) -> dict[str, Any]:
    session_id = new_session_id(run_tag)
    for prior in case.get("context") or []:
        setup = client.detect(session_id, prior, None)
        if setup.error:
            obs = m.error_observation(f"setup turn {prior!r} failed: {setup.error}")
            rec = m.score_case(case, obs)
            rec["session_id"] = session_id
            return rec
    obs = client.detect(session_id, case["text"], case.get("input_contexts"))
    rec = m.score_case(case, obs)
    rec["session_id"] = session_id
    return rec


def run_conversation(client: NLUClient, conv: dict[str, Any], run_tag: str) -> dict[str, Any]:
    session_id = new_session_id(run_tag)
    observations = []
    for turn in conv["turns"]:
        obs = client.detect(session_id, turn["text"], turn.get("input_contexts"))
        observations.append(obs)
        if obs.error:
            break
    rec = m.score_conversation(conv, observations)
    rec["session_id"] = session_id
    return rec


def planned_calls(cases: list[dict[str, Any]], convs: list[dict[str, Any]]) -> int:
    return sum(1 + len(c.get("context") or []) for c in cases) + sum(len(c["turns"]) for c in convs)


def run_all(
    client: NLUClient,
    cases: list[dict[str, Any]],
    convs: list[dict[str, Any]],
    *,
    concurrency: int = 4,
    run_tag: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Run every case and conversation; turns within one session stay sequential."""
    run_tag = run_tag or uuid.uuid4().hex[:6]
    counter = CallCounter()
    counted = counter.wrap(client)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        case_futs = [pool.submit(run_case, counted, c, run_tag) for c in cases]
        conv_futs = [pool.submit(run_conversation, counted, c, run_tag) for c in convs]
        case_recs = [f.result() for f in case_futs]
        conv_recs = [f.result() for f in conv_futs]
    return case_recs, conv_recs, counter.n


# ------------------------------------------------------------------ rescore


class ReplayClient:
    """Feeds stored observations back (offline re-scoring after a label fix)."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.cases = {r["id"]: r["observed"] for r in results.get("cases", [])}
        self.convs = {
            c["id"]: [t["observed"] for t in c["turns"]] for c in results.get("conversations", [])
        }


def rescore(
    stored: dict[str, Any], cases: list[dict[str, Any]], convs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    replay = ReplayClient(stored)
    case_recs = []
    for case in cases:
        raw = replay.cases.get(case["id"])
        obs = m.Observation.from_dict(raw) if raw else m.error_observation("not in stored results")
        rec = m.score_case(case, obs)
        case_recs.append(rec)
    conv_recs = []
    for conv in convs:
        raws = replay.convs.get(conv["id"]) or []
        obs_list = [m.Observation.from_dict(r) for r in raws][: len(conv["turns"])]
        if len(obs_list) < len(conv["turns"]) and not raws:
            obs_list = [m.error_observation("not in stored results")]
        conv_recs.append(m.score_conversation(conv, obs_list))
    return case_recs, conv_recs


# --------------------------------------------------------------------- main


def build_results(
    *,
    meta: dict[str, Any],
    case_recs: list[dict[str, Any]],
    conv_recs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "meta": meta,
        "summary": m.summarize(case_recs, conv_recs),
        "cases": case_recs,
        "conversations": conv_recs,
    }


def _display_path(path: Path) -> str:
    """`path` relative to the repository when possible, so reports carry no home directory."""
    try:
        return path.resolve().relative_to(EVALS_DIR.parent).as_posix()
    except ValueError:
        return path.name


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="single-turn golden YAML")
    p.add_argument(
        "--conversations", type=Path, default=DEFAULT_CONVERSATIONS, help="multi-turn golden YAML"
    )
    p.add_argument("--no-cases", action="store_true", help="skip single-turn cases")
    p.add_argument("--no-conversations", action="store_true", help="skip conversations")
    p.add_argument(
        "--limit", type=int, default=None, help="only the first N cases and first N conversations"
    )
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument(
        "--out", type=Path, default=None, help="output dir (default evals/reports/<UTC timestamp>)"
    )
    p.add_argument(
        "--project", default=None, help="GCP project that hosts the agent (default $GCP_PROJECT)"
    )
    p.add_argument(
        "--project-label",
        default=None,
        help="write this instead of the project id into report.md and results.json "
        "(e.g. a placeholder before committing a report)",
    )
    p.add_argument("--environment", default=None, help="Dialogflow environment (default: draft)")
    p.add_argument("--location", default="global")
    p.add_argument("--language", default="en")
    p.add_argument(
        "--credentials-file", default=None, help="service-account key file (never logged)"
    )
    p.add_argument(
        "--gcloud",
        action="store_true",
        help="use the gcloud CLI account's access token (e.g. for a staging project the server key cannot reach)",
    )
    p.add_argument(
        "--max-calls", type=int, default=350, help="refuse to run if the plan needs more API calls"
    )
    p.add_argument(
        "--max-qpm",
        type=float,
        default=100.0,
        help="client-side cap on API calls per minute (0 = off)",
    )
    p.add_argument(
        "--min-accuracy", type=float, default=0.0, help="exit 1 if intent accuracy is below this"
    )
    p.add_argument("--baseline", type=Path, default=None, help="results.json to compare against")
    p.add_argument(
        "--max-drop", type=float, default=0.02, help="allowed drop vs --baseline (absolute)"
    )
    p.add_argument(
        "--rescore", type=Path, default=None, help="re-score a stored results.json offline"
    )
    p.add_argument("--dry-run", action="store_true", help="print the call plan and exit")
    p.add_argument(
        "--note", action="append", default=[], help="note to add to the report (repeatable)"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None, client: NLUClient | None = None) -> int:
    import os

    args = parse_args(argv)
    try:
        cases = [] if args.no_cases else m.load_cases(args.cases)
        convs = [] if args.no_conversations else m.load_conversations(args.conversations)
    except (OSError, m.GoldenSetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    fingerprint = m.golden_fingerprint({"cases": cases, "conversations": convs})
    if args.limit is not None:
        cases, convs = cases[: args.limit], convs[: args.limit]

    calls = planned_calls(cases, convs)
    print(f"plan: {len(cases)} cases, {len(convs)} conversations, {calls} API calls")
    if args.dry_run:
        return 0

    project = args.project or os.environ.get("GCP_PROJECT") or ""
    if not project and not args.rescore and client is None:
        print("error: pass --project or set GCP_PROJECT", file=sys.stderr)
        return 3
    started = datetime.now(timezone.utc)
    run_name = args.out.name if args.out else started.strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or (EVALS_DIR / "reports" / run_name)

    meta: dict[str, Any] = {
        "run_name": run_name,
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "project": args.project_label or project,
        "environment": args.environment,
        "location": args.location,
        "language": args.language,
        "concurrency": args.concurrency,
        "max_qpm": args.max_qpm,
        "golden_fingerprint": fingerprint,
        "cases_file": args.cases.name,
        "conversations_file": args.conversations.name,
        "limit": args.limit,
        "notes": list(args.note),
    }

    if args.rescore:
        stored = json.loads(args.rescore.read_text(encoding="utf-8"))
        case_recs, conv_recs = rescore(stored, cases, convs)
        for key in (
            "started_at",
            "project",
            "environment",
            "location",
            "language",
            "concurrency",
            "api_calls",
        ):
            if key in stored.get("meta", {}):
                meta[key] = stored["meta"][key]
        meta["rescored_from"] = _display_path(args.rescore)
        meta["notes"] = list(stored.get("meta", {}).get("notes", [])) + list(args.note)
        if args.project_label:
            meta["project"] = args.project_label
    else:
        if calls > args.max_calls:
            print(
                f"error: plan needs {calls} calls > --max-calls {args.max_calls}", file=sys.stderr
            )
            return 3
        if client is None:
            try:
                client = DialogflowESClient(
                    project,
                    language=args.language,
                    environment=args.environment,
                    location=args.location,
                    credentials_file=args.credentials_file,
                    use_gcloud=args.gcloud,
                    max_per_minute=args.max_qpm,
                )
            except Exception as exc:
                print(
                    f"error: could not create Dialogflow client: {type(exc).__name__}",
                    file=sys.stderr,
                )
                return 3
        t0 = time.perf_counter()
        case_recs, conv_recs, made = run_all(client, cases, convs, concurrency=args.concurrency)
        meta["api_calls"] = made
        meta["wall_seconds"] = round(time.perf_counter() - t0, 1)

    results = build_results(meta=meta, case_recs=case_recs, conv_recs=conv_recs)

    exit_code = 0
    gate_msgs: list[str] = []
    s = results["summary"]
    if s["intent_accuracy"] is not None and s["intent_accuracy"] < args.min_accuracy:
        gate_msgs.append(
            f"FAIL intent accuracy {s['intent_accuracy']:.3f} < --min-accuracy {args.min_accuracy:.3f}"
        )
        exit_code = 1
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        ok, msgs = m.compare_to_baseline(results, baseline, args.max_drop)
        gate_msgs.extend(msgs)
        if not ok:
            exit_code = exit_code or 2
    results["gate"] = {"exit_code": exit_code, "messages": gate_msgs}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = m.render_report(results)
    if gate_msgs:
        report += "\n## Gate\n\n" + "\n".join(f"- {g}" for g in gate_msgs) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    def _v(x: float | None, fmt: str = ".3f") -> str:
        return "n/a" if x is None else format(x, fmt)

    sl = s["slots"]
    print(
        f"intent accuracy {_v(s['intent_accuracy'])}  macro F1 {_v(s['macro_f1'])}  "
        f"case pass {_v(s['case_pass_rate'])}  food {_v(sl['food_Item']['accuracy'])}  "
        f"number {_v(sl['number']['accuracy'])}  conversations {s['conversations']['passed']}/"
        f"{s['conversations']['n']}  p50 {_v(s['latency']['p50_ms'], '.0f')} ms  "
        f"p95 {_v(s['latency']['p95_ms'], '.0f')} ms  errors {s['errors']}"
    )
    for g in gate_msgs:
        print(g)
    print(f"wrote {out_dir / 'report.md'} and results.json")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
