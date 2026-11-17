# NLU evals for the Dialogflow ES agent

This harness measures how well the food-ordering agent (Dialogflow ES v2, draft) understands users. It calls Dialogflow directly with `SessionsClient.detect_intent`. It does **not** go through the MCP server, so a run writes nothing to the production database.

| File | What it is |
|---|---|
| `golden/nlu_single_turn.yaml` | Dev set: 182 labelled single-turn utterances covering all 8 intents |
| `golden/conversations.yaml` | Dev set: 16 multi-turn scenarios with per-turn expectations |
| `golden/heldout.yaml` | Held-out set: 54 single-turn utterances and 5 conversations, never used to change the agent |
| `run_nlu.py` | CLI: runs a golden set, writes `report.md` and `results.json`, can gate CI |
| `nlu_metrics.py` | Pure scoring, metrics and report code (no network; unit tested) |
| `leakage.py` | Overlap between golden utterances and the agent's training phrases (see below) |
| `agents/baseline-2026-11-17/` | The pre-fix agent export, to reproduce the "before" run |
| `reports/baseline-2026-11-17/` | Before: dev set against the production agent before the fixes (intent accuracy 73.6%) |
| `reports/prod-after-fixes-2026-11-17/` | After: dev set against the production agent once the training-phrase fixes were validated on a staging copy and promoted (98.4%). This is the baseline for the regression gate |
| `reports/staging-dev-2026-11-17/` | Dev set against the staging copy of the fixed agent (98.4%), the same day as the held-out run |
| `reports/staging-heldout-2026-11-17/` | Held-out set against the same staging agent, run once (87.0%) |

The fixes were iterated on a staging copy of the agent; the intermediate staging runs are not kept. Because the dev set guided the fixes, its after number is a development-set result. The held-out number is the estimate for unseen phrasing. Before the held-out run, the staging and production agents were exported and their intents and entities compared: they are identical (only the display name and the unused webhook URL differ).

The two "before/after" reports were re-scored offline (`--rescore`, no API calls) after the verbatim labels below were added, so they show the metrics with and without those cases. Their observations, latency and call counts are from the original runs.

## Dev set, held-out set and training-phrase overlap

- **Verbatim cases.** 11 dev-set utterances are, after normalising case, punctuation, "&" and whitespace, also training phrases of the current agent (for example "hi", "new order", "track order 1005"). Some were added as fixes for failures this set found. They are labelled `verbatim_in_training: true`, and every report repeats intent accuracy, macro F1 and case pass rate without them. `leakage.py` computes the label from `dialogflow/agent/`; a test and the CI lint job fail if the labels drift.
- **Held-out set.** Written after the fixes by a writer who was given only the menu, the intent and context names, the label conventions and the tag list (not the training phrases, the dev set or any results). The writer had no access to the repository, but its brief was written by someone who had seen the training phrases and the dev set, and it included a few style hints, some local dish nicknames and one example utterance, so the set is independent of the dev set but not perfectly blind. The draft had 70 cases and 5 conversations. Every utterance with a near match to a training phrase or a dev-set utterance (normalised text equal, or `difflib` ratio >= 0.85) was dropped mechanically: 16 cases and 2 closing conversation turns ("done", "thats it"). Nothing was relabelled after the run, and the agent was not changed for it. Never add held-out utterances to the training phrases; once the agent is tuned against this set's failures, write a new held-out set.
- **Reproducing the baseline.** Restore `agents/baseline-2026-11-17/` into a **staging** project, wait for training, run the dev set, then restore `dialogflow/agent/` again:
  ```bash
  uv run python scripts/dialogflow_agent.py restore --project <staging> --confirm <staging> --dir evals/agents/baseline-2026-11-17
  uv run python evals/run_nlu.py --project <staging> --out evals/reports/baseline-repro
  uv run python scripts/dialogflow_agent.py restore --project <staging> --confirm <staging>
  ```
  Dialogflow ES is not fully deterministic across trainings, so expect the result to be close to 73.6% rather than identical. The export has its webhook URL and the legacy V1 key blanked; fulfilment is off, so neither is needed.

The labels describe what a **good product should do**, not what the agent does today. A failure is a product gap. For example, "hi" was labelled `Default Welcome Intent` while the agent still answered it with the fallback (fixed on 2026-11-17).

## What it measures

- **Intent accuracy** and **macro F1** over the single-turn cases. Per-intent precision, recall, F1 and support, plus a confusion matrix (rows are expected, columns are predicted).
- **Case pass rate.** A case passes when the intent is right, every labelled slot is right, and the agent did not ask for a slot the label says was given.
- **Slot accuracy.** `food_Item` is a multiset match and ignores order. `number` is the list of quantities in utterance order, aligned with the food items (for `track.order` it is the order id). Both are reported two ways:
  - **accuracy:** the extraction match on every case that labels the slot, whether or not the intent was right. A slot can be extracted correctly under the wrong intent; for example, "a kota" gives `food_Item [Kota]` but `order_remove`.
  - **accuracy given intent:** only over cases where the intent was right, which is what the server would actually act on.
- **Tag accuracy.** Covers slang, typo, multi-item, a/an-quantity, off-menu, paraphrase, numbers-as-words, track-id, chit-chat, and others. This shows which kinds of phrasing break.
- **Latency.** p50, p95, max and mean of the scored `detect_intent` calls (setup turns are excluded). Only the final, successful attempt of a retried call is timed.
- **Conversations.** Pass rate (every turn must pass), turn pass rate, and the first failing turn of each scenario.

### Label conventions

| Field | Meaning |
|---|---|
| `expected_food_Item: [...]` | Exact menu names. `[]` means the agent must **not** extract any food (off-menu). If the field is omitted, the slot is not checked. |
| `expected_number` | For `order.add`, a list aligned with the items. "a"/"an" counts as 1, so "a kota and 2 bunny chows" is `[1, 2]`. For `track.order*`, an integer id. |
| `allow_slot_prompt: true` | A required slot is genuinely missing ("track order", "I want bunny chow"), so a prompt (`all_required_params_present = false`) is a correct answer. Without this flag, an unexpected prompt fails the case. |
| `input_contexts: [ongoing-order]` | The context is injected through `QueryParameters.contexts`. This is the same matching condition as a prior "new order" turn but costs one API call instead of two. |
| `context: ["new order"]` | Real prior turns are sent in the same session. A few cases use this to check that it behaves the same as injected contexts. |

Slot filling consumes the next turn. In "track my order" → "What is your order id?" → "1005", the "1005" turn is matched to `track.order`, not to `track.order - more`. The conversation file encodes this.

## How to run

```bash
# credentials: a key with the "Dialogflow API Client" role is enough
uv run python evals/run_nlu.py --dry-run                       # prints the plan: cases, conversations, API calls
uv run python evals/run_nlu.py --project your-gcp-project-id --limit 5 --credentials-file /path/to/key.json   # smoke test (~15 calls)
uv run python evals/run_nlu.py --project your-gcp-project-id --credentials-file /path/to/key.json             # full run (~243 calls, ~2.5 min)
uv run python evals/run_nlu.py --project your-gcp-project-id --cases evals/golden/heldout.yaml --conversations evals/golden/heldout.yaml   # held-out (~68 calls)
```

`heldout.yaml` holds both `cases:` and `conversations:`, so pass it to both flags.

Credentials are resolved in this order: `--gcloud` (the gcloud CLI account's token, useful for a staging project the server key cannot reach), `--credentials-file`, then `GOOGLE_APPLICATION_CREDENTIALS_JSON` (the key *content*, same as the server), then Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` path, gcloud ADC, ...). The key path is never written to the report.

Other useful flags:

| Flag | Default | Use |
|---|---|---|
| `--cases`, `--conversations` | `golden/*.yaml` | Point at another golden set |
| `--no-cases`, `--no-conversations` | off | Run only one half |
| `--limit N` | all | First N cases and first N conversations |
| `--concurrency` | 4 | Parallel sessions; turns within one session are always sequential |
| `--max-qpm` | 100 | Client-side cap on calls per minute (see quota notes) |
| `--max-calls` | 350 | Refuses to start if the plan needs more calls |
| `--project-label TEXT` | | Written to the report instead of the project id (use it for any report you commit) |
| `--project`, `--environment`, `--location`, `--language` | `$GCP_PROJECT` (required for a live run), draft, `global`, `en` | Target another agent, for example a staging project or a published environment |
| `--out DIR` | `evals/reports/<UTC timestamp>` | Output directory |
| `--rescore results.json` | | Re-score stored observations against the current labels **without calling Dialogflow**. Use it after fixing a label. |
| `--note TEXT` | | Adds a note to the report (repeatable) |

Every session id is `eval-<run>-<random>`, so eval traffic is easy to spot in Dialogflow history.

## Reading the report

1. **Headline.** Intent accuracy is the gate metric. Case pass rate is stricter because it includes slots.
2. **Confusion matrix.** Read across a row to see where an intent's utterances end up. A large off-diagonal cell is usually a missing set of training phrases. Some cells matter more than others: `order.add → order_remove` actually removes an item, and `new.order → track.order` asks a new customer for an order id.
3. **Slot accuracy.** Low accuracy means the entity, synonyms or annotations are the problem. Read it together with the confusion matrix: a correctly extracted slot under the wrong intent still does the wrong thing.
4. **By tag.** Low pass rates on `a/an-quantity`, `paraphrase` or `slang` point at whole categories of missing training phrases.
5. **Failures.** One row per failed case: expected vs. got, plus the agent's reply.
6. **Conversations.** The first failing turn is the one to fix. Later turns often fail only as a consequence.

`results.json` holds the same data plus every raw observation (intent, confidence, normalised params, `all_required_params_present`, reply, latency, session id), so you can diff runs case by case or re-score them offline.

## Becoming a CI gate

The flow for agent changes (`dialogflow/agent/` is the source of truth). [`.github/workflows/evals.yml`](../.github/workflows/evals.yml) runs steps 1-3 on demand (`workflow_dispatch`) once the `GCP_EVAL_SA_KEY` secret and `GCP_EVAL_PROJECT` variable are set:

1. **Restore the PR's agent into a staging project**, never into production:
   `uv run python scripts/dialogflow_agent.py restore --project <staging> --confirm <staging>`
   This needs a *Dialogflow API Admin* identity for the staging project only. ES trains after a restore, so wait for training to finish before running the eval.
2. **Run the eval against staging and compare with the committed baseline:**
   ```bash
   uv run python evals/run_nlu.py --project <staging> \
       --baseline evals/reports/prod-after-fixes-2026-11-17/results.json --max-drop 0.02 \
       --min-accuracy 0.95 --out evals/reports/ci-$GITHUB_RUN_ID
   ```
   Exit codes: `0` ok, `1` intent accuracy below `--min-accuracy`, `2` intent accuracy or conversation pass rate dropped more than `--max-drop` against the baseline, `3` bad input or setup error. Per-intent F1 drops are printed as warnings. The gate only warns because supports of 8-20 make per-intent F1 noisy.
3. **Upload `report.md` as a job artifact**, or post it as a PR comment.
4. **When a change is an intended improvement, commit the new `results.json` as the next baseline.** `results.json` records a golden-set fingerprint. If the golden set changed, the comparison prints a warning that it is not like-for-like. In that case, re-baseline from `main` on the new set first.

The unit tests (`tests/test_evals_metrics.py`) already run in the normal `uv run pytest` with a fake client and no GCP access. They also validate the golden files: valid intents, exact menu names, unique ids, size, and the call budget.

## Cost and quota notes

- Dialogflow ES text requests are free on the Trial edition but are capped **per minute**. The first baseline attempt ran 243 calls in 47 s (about 300/min), got 4 `429 RESOURCE_EXHAUSTED` responses, and its retries (about 15 s in total) did not reach the next minute window. The runner now spaces calls to `--max-qpm 100` by default and retries 429/5xx with exponential backoff and jitter for up to about 60 s. A full run takes about 2.5 minutes.
- A full dev-set run is about 243 calls: 182 cases, 7 setup turns and 54 conversation turns. The held-out set is 68 calls. Keep the golden set to roughly 300 calls or fewer per run. Injecting contexts (`input_contexts`) rather than sending setup turns is what keeps it there. `--dry-run` prints the count, and `--max-calls` enforces it.
- Re-scoring (`--rescore`) after a label fix costs nothing.
- On the Essentials (paid) edition, text requests are billed per request. At about 243 calls a run, a nightly job costs very little, but don't run the eval on every push.
