# NLU eval report: staging-dev-2026-11-17

Agent `your-gcp-project-id` (Dialogflow ES, language `en`, draft). Run at 2026-11-17 20:19:23 UTC, 243 API calls, concurrency 4. Golden set fingerprint `cbe18b9d45516abf`.

## Headline

| Metric | Value |
|---|---|
| Single-turn cases | 182 |
| Intent accuracy | 98.4% |
| Macro F1 (intents) | 0.98 |
| Case pass rate (intent + slots + prompt) | 95.6% |
| Without the 11 cases verbatim in training (171 cases): intent accuracy / macro F1 / case pass rate | 98.2% / 0.98 / 95.3% |
| food_Item accuracy | 99.0% (99/100) |
| number accuracy | 93.2% (82/88) |
| Conversation pass rate | 81.2% (13/16) |
| Conversation turn pass rate | 94.4% |
| Unexpected slot prompts | 0 |
| API errors (after retries) | 0 |
| Latency p50 / p95 / max | 365 ms / 624 ms / 1225 ms (n=236) |

## Per-intent precision / recall / F1

| Intent | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Default Fallback Intent | 20 | 0.91 | 1.00 | 0.95 |
| Default Welcome Intent | 10 | 1.00 | 1.00 | 1.00 |
| new.order | 18 | 1.00 | 1.00 | 1.00 |
| order.add | 72 | 1.00 | 0.99 | 0.99 |
| order.complete | 16 | 1.00 | 0.94 | 0.97 |
| order_remove | 18 | 1.00 | 1.00 | 1.00 |
| track.order | 20 | 0.95 | 1.00 | 0.98 |
| track.order - more | 8 | 1.00 | 0.88 | 0.93 |

## Confusion matrix

Rows = expected, columns = predicted.

| expected \ predicted | fallback | welcome | new.order | order.add | order.complete | order_remove | track.order | track.more |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fallback | **20** | . | . | . | . | . | . | . |
| welcome | . | **10** | . | . | . | . | . | . |
| new.order | . | . | **18** | . | . | . | . | . |
| order.add | 1 | . | . | **71** | . | . | . | . |
| order.complete | 1 | . | . | . | **15** | . | . | . |
| order_remove | . | . | . | . | . | **18** | . | . |
| track.order | . | . | . | . | . | . | **20** | . |
| track.more | . | . | . | . | . | . | 1 | **7** |

## Slot accuracy

`accuracy` is the extraction match on every case that labels the slot, regardless of the intent (a slot can be right under the wrong intent). `given intent` only counts cases where the intent was right, i.e. what the server would actually act on.

| Slot | Checked | Accuracy | Checked (intent right) | Accuracy given intent |
|---|---:|---:|---:|---:|
| food_Item | 100 | 99.0% | 99 | 100.0% |
| number | 88 | 93.2% | 86 | 94.2% |
| all labelled slots | 121 | 95.0% | | |

## By tag

| Tag | Cases | Intent accuracy | Pass rate |
|---|---:|---:|---:|
| a/an-quantity | 12 | 100.0% | 75.0% |
| chit-chat | 19 | 100.0% | 100.0% |
| exact-phrase | 4 | 100.0% | 100.0% |
| missing-quantity | 5 | 100.0% | 100.0% |
| multi-item | 16 | 100.0% | 75.0% |
| no-context | 1 | 100.0% | 100.0% |
| numbers-as-words | 15 | 100.0% | 93.3% |
| off-menu | 12 | 100.0% | 91.7% |
| paraphrase | 66 | 98.5% | 98.5% |
| slang | 25 | 96.0% | 96.0% |
| synonym | 16 | 100.0% | 100.0% |
| track-id | 21 | 95.2% | 90.5% |
| typo | 21 | 90.5% | 90.5% |
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
| conv-09-missing-quantity-slot-fill | FAIL | 3/4 | 3: "2" (intent order.add -> Default Fallback Intent; food_Item ['Bunny Chow'] -> []; number [2] -> []) |
| conv-10-greet-then-order | PASS | 4/4 |  |
| conv-11-remove-before-order | PASS | 3/3 |  |
| conv-12-done-before-order-then-track | PASS | 2/2 |  |
| conv-13-slang-order | FAIL | 3/4 | 4: "dats it" (intent order.complete -> Default Fallback Intent) |
| conv-14-chit-chat-mid-order | PASS | 4/4 |  |
| conv-15-track-then-order | PASS | 4/4 |  |
| conv-16-numbers-as-words | PASS | 4/4 |  |

## Failures (8)

| Case | Text | Tags | What went wrong | Reply (truncated) |
|---|---|---|---|---|
| add-25 | a kota and 2 bunny chows | a/an-quantity, multi-item | number [1, 2] -> [2] | Added Kota and Bunny Chow to your order. Anything else? |
| add-26 | a milk tart and a vetkoek | a/an-quantity, multi-item | number [1, 1] -> [1] | Got it, Milk Tart and Vetkoek added. Would you like anything else? |
| add-44 | 3 vetcook | typo | intent order.add -> Default Fallback Intent; food_Item ['Vetkoek'] -> []; number [3] -> [] | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| add-57 | 2 kotas plus a bunny chow | multi-item, a/an-quantity | number [2, 1] -> [2] | Got it, Kota and Bunny Chow added. Would you like anything else? |
| add-72 | 1 bobotie and 2 burgers | off-menu, multi-item | number [1] -> [1, 2] | Added Bobotie to your order. Anything else? |
| cmp-13 | dats it | slang, typo | intent order.complete -> Default Fallback Intent | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| trk-08 | track order one thousand and five | track-id, numbers-as-words | number [1005] -> [1000] | Got it. Checking the status of order #1000. |
| trm-05 | also order 42 | track-id, paraphrase | intent track.order - more -> track.order | Got it. Checking the status of order #42. |

## Notes

- Dev set (182 cases, 16 conversations) against the staging copy of the agent, whose intents and entities were confirmed identical to production by export and diff on 2026-11-17.
