# NLU evals for the Dialogflow ES agent

This harness measures how well the food-ordering agent (Dialogflow ES v2, draft) understands users. It calls Dialogflow directly with `SessionsClient.detect_intent`. It does **not** go through the MCP server, so a run writes nothing to the production database.

| File | What it is |
|---|---|
| `golden/nlu_single_turn.yaml` | Dev set: 212 labelled single-turn utterances covering all 9 intents (30 tagged `added_2026-11-17b`, added in 1.1.0) |
| `golden/conversations.yaml` | Dev set: 16 multi-turn scenarios with per-turn expectations |
| `golden/heldout.yaml` | Held-out set: 54 single-turn utterances and 5 conversations over the 8 intents of v1.0.0, never used to change the agent and never edited |
| `run_nlu.py` | CLI: runs a golden set, writes `report.md` and `results.json`, can gate CI |
| `nlu_metrics.py` | Pure scoring, metrics and report code (no network; unit tested) |
| `leakage.py` | Overlap between golden utterances and the agent's training phrases (see below) |
| `agents/baseline-2026-11-17/` | The pre-fix agent export, to reproduce the "before" run |
| `reports/baseline-2026-11-17/` | Before: dev set against the production agent before the fixes (intent accuracy 73.6%) |
| `reports/prod-after-fixes-2026-11-17/` | After: dev set against the production agent once the training-phrase fixes were validated on a staging copy and promoted (98.4%). This was the regression-gate baseline until v1.1.0 |
| `reports/staging-dev-2026-11-17/` | Dev set against the staging copy of the fixed agent (98.4%), the same day as the held-out run |
| `reports/staging-heldout-2026-11-17/` | Held-out set against the same staging agent, run once (87.0%) |
| `reports/v1.1.0-dev/` | v1.1.0: dev set (212 cases) against the production agent that matches `dialogflow/agent/` (99.5%). This is the baseline for the regression gate |
| `reports/v1.1.0-heldout/` | v1.1.0: held-out set against the same production agent, run once (88.9%) |

The fixes were iterated on a staging copy of the agent; the intermediate staging runs are not kept. Because the dev set guided the fixes, its after number is a development-set result. The held-out number is the estimate for unseen phrasing. Before the held-out run, the staging and production agents were exported and their intents and entities compared: they are identical (only the display name and the unused webhook URL differ).

The two "before/after" reports were re-scored offline (`--rescore`, no API calls) after the verbatim labels below were added, so they show the metrics with and without those cases. Their observations, latency and call counts are from the original runs.

## Dev set, held-out set and training-phrase overlap

- **Verbatim cases.** 13 dev-set utterances are, after normalising case, punctuation, "&" and whitespace, also training phrases of the current agent (for example "hi", "new order", "track order 1005"). Some were added as fixes for failures this set found. They are labelled `verbatim_in_training: true`, and every report repeats intent accuracy, macro F1 and case pass rate without them. `leakage.py` computes the label from `dialogflow/agent/`; a test and the CI lint job fail if the labels drift.
- **Held-out set.** Written after the fixes by a writer who was given only the menu, the intent and context names, the label conventions and the tag list (not the training phrases, the dev set or any results). The writer had no access to the repository, but its brief was written by someone who had seen the training phrases and the dev set, and it included a few style hints, some local dish nicknames and one example utterance, so the set is independent of the dev set but not perfectly blind. The draft had 70 cases and 5 conversations. Every utterance with a near match to a training phrase or a dev-set utterance (normalised text equal, or `difflib` ratio >= 0.85) was dropped mechanically: 16 cases and 2 closing conversation turns ("done", "thats it"). Nothing was relabelled after the run, and the agent was not changed for it. Never add held-out utterances to the training phrases; once the agent is tuned against this set's failures, write a new held-out set.
- **Known held-out overlap (since 1.1.0).** The 1.1.0 agent update added "thats all thanks" as an `order.complete` training phrase, which is also held-out case `ho-complete-01`. The phrases were written in a private repository that never contained the held-out set, so the overlap is a coincidence, and the case already passed against the v1.0.0 agent. The held-out file is not edited; the overlap is listed in `leakage.HELDOUT_KNOWN_OVERLAP`, any other near match still fails `leakage.py --strict` and the tests, and the numbers are given with and without the case (see below).
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
- **Slot accuracy.** `food_Item` is a multiset match and ignores order. If the agent returns a composite `order_line` parameter (a list of `{food_Item, number}` pairs), the flat `food_Item` and `number` are derived from it with the same rule as the MCP server (`nlu_metrics.derive_flat_from_order_line`: aligned, a missing number is 1), and the report adds a raw `order_line` row that checks the (dish, quantity) pairs in utterance order without that default. The current agent returns flat parameters only, so the v1.1.0 reports have no such row. `number` is the list of quantities in utterance order, aligned with the food items (for `track.order` it is the order id). Both are reported two ways:
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
uv run python evals/run_nlu.py --project your-gcp-project-id --credentials-file /path/to/key.json             # full run (~273 calls, ~3 min)
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
       --baseline evals/reports/v1.1.0-dev/results.json --max-drop 0.02 \
       --min-accuracy 0.95 --out evals/reports/ci-$GITHUB_RUN_ID
   ```
   Exit codes: `0` ok, `1` intent accuracy below `--min-accuracy`, `2` intent accuracy or conversation pass rate dropped more than `--max-drop` against the baseline, `3` bad input or setup error. Per-intent F1 drops are printed as warnings. The gate only warns because supports of 8-20 make per-intent F1 noisy.
3. **Upload `report.md` as a job artifact**, or post it as a PR comment.
4. **When a change is an intended improvement, commit the new `results.json` as the next baseline.** `results.json` records a golden-set fingerprint. If the golden set changed, the comparison prints a warning that it is not like-for-like. In that case, re-baseline from `main` on the new set first.

The unit tests (`tests/test_evals_metrics.py`) already run in the normal `uv run pytest` with a fake client and no GCP access. They also validate the golden files: valid intents, exact menu names, unique ids, size, and the call budget.

## Cost and quota notes

- Dialogflow ES text requests are free on the Trial edition but are capped **per minute**. The first baseline attempt ran 243 calls in 47 s (about 300/min), got 4 `429 RESOURCE_EXHAUSTED` responses, and its retries (about 15 s in total) did not reach the next minute window. The runner now spaces calls to `--max-qpm 100` by default and retries 429/5xx with exponential backoff and jitter for up to about 60 s. A full dev-set run takes about 3 minutes.
- A full dev-set run is about 273 calls: 212 cases, 7 setup turns and 54 conversation turns. The held-out set is 68 calls. Keep the golden set to roughly 300 calls or fewer per run. Injecting contexts (`input_contexts`) rather than sending setup turns is what keeps it there. `--dry-run` prints the count, and `--max-calls` enforces it.
- Re-scoring (`--rescore`) after a label fix costs nothing.
- On the Essentials (paid) edition, text requests are billed per request. At about 273 calls a run, a nightly job costs very little, but don't run the eval on every push.

## v1.1.0 agent update (2026-11-17)

The round targeted the failures left in `prod-after-fixes-2026-11-17`. Changes were made on a staging copy first, then promoted to production behind the same gate. They were written against the dev set only; no held-out case was used.

What changed in `dialogflow/agent/` (flat parameters, backward compatible):

- **New intent `menu.ask`.** 43 training phrases for availability ("is the kota available"), price ("how much is a bunny chow") and menu questions ("what's on the menu"). It has an optional list parameter `food_Item`, no `number` and no contexts in or out, and its static reply lists the menu. It never adds to the order: these questions are no longer captured by `order.add`, with or without the `ongoing-order` context.
- **`order.complete`:** 20 more completion phrases ("dats it bru", "no thats all", "that will be everything", ...).
- **`food_Item` synonyms:** vetcook, vetcooks and fetkoek for Vetkoek; quarter, half and full bunny for Bunny Chow ("quarter" is a size, not 0.25). Multi-word variants such as "vet koek" were tried and dropped, because fuzzy matching then extracted Vetkoek from "a coke".
- **`order.add`:** 12 more phrases (off-menu mixes such as "3 kotas and a sprite", quarter bunnies, mixed a/digit quantities, measure words). The schema is unchanged: `food_Item` plus an optional `number` with a default of 1.

Golden-set changes:

- 30 dev cases tagged `added_2026-11-17b`: mixed a/digit quantities, "a couple"/"a dozen"/"a single", quarter bunny, vetcook, completion slang, 10 `menu.ask` cases and 2 measure words. `menu.ask` was added to `nlu_metrics.INTENTS`. Before the agent changes, 11 of the 30 passed on staging (a development run whose report is not included here); in `reports/v1.1.0-dev/`, 24 of 30 pass. The 6 that still fail are all mixed quantities.
- **conv-09 relabelled.** The quantity is optional with a default of 1, so "I want bunny chow" adds one Bunny Chow and the bot never asks "how many?". A following bare "2" answers no pending question, so it is now labelled `Default Fallback Intent` with no food extracted: the agent must not guess. The old label expected a slot-filling flow the product no longer has, so conversation counts before and after are not like-for-like.
- `heldout.yaml` is unchanged.

Results on production (`reports/v1.1.0-dev/`, `reports/v1.1.0-heldout/`), compared with v1.0.0:

| Check | v1.0.0 agent | v1.1.0 agent |
|---|---:|---:|
| Dev intent accuracy, the original 182 cases | 98.4% | 99.5% |
| Dev case pass rate, the original 182 cases | 95.6% | 96.7% |
| Dev `number` accuracy, the original 182 cases | 93.2% | 94.3% |
| Dev intent accuracy, all 212 cases / without the 13 verbatim cases | | 99.5% / 99.5% |
| Dev conversations (conv-09 relabelled in 1.1.0) | 13/16 | 15/16 |
| Held-out intent accuracy, 54 cases | 87.0% (47/54) | 88.9% (48/54) |
| Held-out intent accuracy without `ho-complete-01`, 53 cases | 86.8% (46/53) | 88.7% (47/53) |
| Held-out case pass rate / conversations | 77.8% / 2/5 | 79.6% / 2/5 |

The v1.0.0 held-out run was against a staging copy of the agent, identical in intents and entities to production at the time; the v1.1.0 runs are against production. The only held-out change is `ho-fallback-03` ("who won the derby on saturday bru"), which now falls back instead of matching the Welcome intent. A one-case difference from one run is within run-to-run noise. Every `menu.ask` dev case passes (F1 1.00).

Still failing:

- **Mixed quantities** (add-25/26/57/73-76/81/84, conv-01, and held-out ho-add-02/07/10/20 and two conversations). `@sys.number` cannot read "a"/"an", and when "a" and a digit are mixed ES returns only the digits ("a kota and 2 bunny chows" → `[2]`); repeated "a" values come back once ("a milk tart and a vetkoek" → `[1]`). A flat `number` list cannot be aligned with `food_Item`.
- add-72 "1 bobotie and 2 burgers" keeps the off-menu quantity (`[1, 2]`).
- trk-08 "track order one thousand and five": `@sys.number-integer` reads 1000. A training-phrase annotation cannot change a system entity's grammar, so parse number words in the backend if it matters.
- trm-05 "also order 42" in a tracking context matches `track.order` instead of `track.order - more`.

### Quantities: the `order_line` parameter

Measured on staging during the round (development runs; their reports are not included in this repository):

1. A word-quantity map entity (a/an/single → 1, a couple of → 2) annotated under the existing `number` parameter (typed `@sys.number`) does not help: with "a" and a digit mixed, ES returns only the `@sys.number` values.
2. Two separate flat lists (`number` and a word-quantity list) carry no pairing, so "a kota and 2 bunny chows" and "2 kotas and a bunny chow" would look the same. Not built.
3. A composite entity `@order_line` (entries such as `@sys.number:number @food_Item:food_Item`, a word quantity followed by `@food_Item`, a bare `@food_Item`, and "N plates/orders/portions of ...") returns aligned pairs in utterance order, for example `[{"food_Item": "Kota", "number": "1"}, {"food_Item": "Bunny Chow", "number": 2}]`. ES cannot also fill the flat `food_Item`/`number` from it, so `order.add` in that variant returns `order_line` only.

A candidate agent built that way scored 99.1% intent accuracy, 98.6% case pass rate, 99.0% `number` accuracy and 16/16 conversations on the dev set on staging, in a development run. That run also showed a regression ("drop the milk tarts and the kota" matched `order.add`); the `order_remove` phrases added for it were only spot-checked, not re-run in full. The candidate is **not promoted** and not included in this repository: consumers that read only the flat parameters would break. 1.1.0 prepares for it instead:

- **Consumer contract.** `order_line` is a list of `{food_Item: <menu name>, number?: <number or "1" string>}`. Derive `food_Item = [line.food_Item, ...]` and `number = [int(line.number) if present else 1, ...]`.
- `dialogflow_mcp.server_es._with_flat_order_params` does this in `detect_intent`: when `order_line` is present it returns the derived flat parameters and `order_line` unchanged; without it, the parameters pass through untouched.
- The harness applies the same rule and reports the raw pair accuracy, so a future run of such an agent scores what consumers see.

Promotion would mean: deploy a server with the adapter (1.1.0 or later) and the same derivation in every other consumer, restore the candidate into staging and gate it against `reports/v1.1.0-dev/results.json`, then promote and re-run, and write a fresh held-out set before quoting a new generalisation number.
