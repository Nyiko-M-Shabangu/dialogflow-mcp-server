# NLU eval report: v1.1.0-dev

Agent `your-gcp-project-id` (Dialogflow ES, language `en`, draft). Run at 2026-11-17 22:30:34 UTC, 273 API calls, concurrency 4. Golden set fingerprint `4206181d94d7df97`.

## Headline

| Metric | Value |
|---|---|
| Single-turn cases | 212 |
| Intent accuracy | 99.5% |
| Macro F1 (intents) | 0.99 |
| Case pass rate (intent + slots + prompt) | 94.3% |
| Without the 13 cases verbatim in training (199 cases): intent accuracy / macro F1 / case pass rate | 99.5% / 0.99 / 94.0% |
| food_Item accuracy | 100.0% (125/125) |
| number accuracy | 89.3% (92/103) |
| Conversation pass rate | 93.8% (15/16) |
| Conversation turn pass rate | 98.1% |
| Unexpected slot prompts | 0 |
| API errors (after retries) | 0 |
| Latency p50 / p95 / max | 352 ms / 484 ms / 1135 ms (n=266) |

## Per-intent precision / recall / F1

| Intent | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Default Fallback Intent | 20 | 1.00 | 1.00 | 1.00 |
| Default Welcome Intent | 10 | 1.00 | 1.00 | 1.00 |
| menu.ask | 10 | 1.00 | 1.00 | 1.00 |
| new.order | 18 | 1.00 | 1.00 | 1.00 |
| order.add | 87 | 1.00 | 1.00 | 1.00 |
| order.complete | 21 | 1.00 | 1.00 | 1.00 |
| order_remove | 18 | 1.00 | 1.00 | 1.00 |
| track.order | 20 | 0.95 | 1.00 | 0.98 |
| track.order - more | 8 | 1.00 | 0.88 | 0.93 |

## Confusion matrix

Rows = expected, columns = predicted.

| expected \ predicted | fallback | welcome | menu.ask | new.order | order.add | order.complete | order_remove | track.order | track.more |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fallback | **20** | . | . | . | . | . | . | . | . |
| welcome | . | **10** | . | . | . | . | . | . | . |
| menu.ask | . | . | **10** | . | . | . | . | . | . |
| new.order | . | . | . | **18** | . | . | . | . | . |
| order.add | . | . | . | . | **87** | . | . | . | . |
| order.complete | . | . | . | . | . | **21** | . | . | . |
| order_remove | . | . | . | . | . | . | **18** | . | . |
| track.order | . | . | . | . | . | . | . | **20** | . |
| track.more | . | . | . | . | . | . | . | 1 | **7** |

## Slot accuracy

`accuracy` is the extraction match on every case that labels the slot, regardless of the intent (a slot can be right under the wrong intent). `given intent` only counts cases where the intent was right, i.e. what the server would actually act on.

| Slot | Checked | Accuracy | Checked (intent right) | Accuracy given intent |
|---|---:|---:|---:|---:|
| food_Item | 125 | 100.0% | 125 | 100.0% |
| number | 103 | 89.3% | 102 | 89.2% |
| all labelled slots | 146 | 92.5% | | |

## By tag

| Tag | Cases | Intent accuracy | Pass rate |
|---|---:|---:|---:|
| a/an-quantity | 21 | 100.0% | 57.1% |
| added_2026-11-17b | 30 | 100.0% | 80.0% |
| chit-chat | 19 | 100.0% | 100.0% |
| exact-phrase | 4 | 100.0% | 100.0% |
| menu-question | 10 | 100.0% | 100.0% |
| missing-quantity | 5 | 100.0% | 100.0% |
| multi-item | 23 | 100.0% | 56.5% |
| no-context | 1 | 100.0% | 100.0% |
| numbers-as-words | 20 | 100.0% | 85.0% |
| off-menu | 13 | 100.0% | 92.3% |
| paraphrase | 71 | 98.6% | 97.2% |
| slang | 30 | 100.0% | 96.7% |
| synonym | 19 | 100.0% | 94.7% |
| track-id | 21 | 95.2% | 90.5% |
| typo | 27 | 100.0% | 100.0% |
| untagged | 14 | 100.0% | 100.0% |

## Conversations

| Conversation | Result | Turns passed | First failing turn |
|---|---|---:|---|
| conv-01-happy-path | FAIL | 2/3 | 2: "2 bunny chows and a milk tart" (number [2, 1] -> [2]) |
| conv-02-add-add-remove-complete | PASS | 5/5 |  |
| conv-03-track-with-id | PASS | 1/1 |  |
| conv-04-track-without-id | PASS | 2/2 |  |
| conv-05-track-then-how-about | PASS | 2/2 |  |
| conv-06-track-prompt-then-follow-up | PASS | 3/3 |  |
| conv-07-off-menu-mid-order | PASS | 4/4 |  |
| conv-08-restart | PASS | 5/5 |  |
| conv-09-missing-quantity-slot-fill | PASS | 4/4 |  |
| conv-10-greet-then-order | PASS | 4/4 |  |
| conv-11-remove-before-order | PASS | 3/3 |  |
| conv-12-done-before-order-then-track | PASS | 2/2 |  |
| conv-13-slang-order | PASS | 4/4 |  |
| conv-14-chit-chat-mid-order | PASS | 4/4 |  |
| conv-15-track-then-order | PASS | 4/4 |  |
| conv-16-numbers-as-words | PASS | 4/4 |  |

## Failures (12)

| Case | Text | Tags | What went wrong | Reply (truncated) |
|---|---|---|---|---|
| add-25 | a kota and 2 bunny chows | a/an-quantity, multi-item | number [1, 2] -> [2] | Added Kota and Bunny Chow to your order. Anything else? |
| add-26 | a milk tart and a vetkoek | a/an-quantity, multi-item | number [1, 1] -> [1] | Got it, Milk Tart and Vetkoek added. Would you like anything else? |
| add-57 | 2 kotas plus a bunny chow | multi-item, a/an-quantity | number [2, 1] -> [2] | Added Kota and Bunny Chow to your order. Anything else? |
| add-72 | 1 bobotie and 2 burgers | off-menu, multi-item | number [1] -> [1, 2] | Added Bobotie to your order. Anything else? |
| add-73 | 2 bunny chows and a milk tart | a/an-quantity, multi-item, added_2026-11-17b | number [2, 1] -> [2] | Added Bunny Chow and Milk Tart to your order. Anything else? |
| add-74 | a bobotie and two sosaties | a/an-quantity, multi-item, numbers-as-words, added_2026-11-17b | number [1, 2] -> [2] | Added Bobotie and Sosaties to your order. Anything else? |
| add-75 | 1 kota and a vetkoek | a/an-quantity, multi-item, added_2026-11-17b | number [1, 1] -> [1] | Got it, Kota and Vetkoek added. Would you like anything else? |
| add-76 | an order of kota and 3 vetkoek | a/an-quantity, multi-item, paraphrase, added_2026-11-17b | number [1, 3] -> [3] | Added Kota and Vetkoek to your order. Anything else? |
| add-81 | a quarter bunny and 2 kotas | slang, synonym, a/an-quantity, multi-item, added_2026-11-17b | number [1, 2] -> [2] | Got it, Bunny Chow and Kota added. Would you like anything else? |
| add-84 | a dozen kotas and a milk tart | numbers-as-words, a/an-quantity, multi-item, added_2026-11-17b | number [12, 1] -> [12] | Got it, Kota and Milk Tart added. Would you like anything else? |
| trk-08 | track order one thousand and five | track-id, numbers-as-words | number [1005] -> [1000] | Got it. Checking the status of order #1000. |
| trm-05 | also order 42 | track-id, paraphrase | intent track.order - more -> track.order | Got it. Checking the status of order #42. |

## Gate

- WARNING: golden set differs from the baseline's (cbe18b9d45516abf -> 4206181d94d7df97); the comparison is not like-for-like.
- ok intent_accuracy: baseline 0.984 -> current 0.995 (drop -0.012, max 0.020)
- ok conversation_pass_rate: baseline 0.812 -> current 0.938 (drop -0.125, max 0.020)
