# NLU eval report: v1.1.0-heldout

Agent `your-gcp-project-id` (Dialogflow ES, language `en`, draft). Run at 2026-11-17 22:33:53 UTC, 68 API calls, concurrency 4. Golden set fingerprint `7363338f35fe5798`.

## Headline

| Metric | Value |
|---|---|
| Single-turn cases | 54 |
| Intent accuracy | 88.9% |
| Macro F1 (intents) | 0.88 |
| Case pass rate (intent + slots + prompt) | 79.6% |
| food_Item accuracy | 96.3% (26/27) |
| number accuracy | 68.0% (17/25) |
| Conversation pass rate | 40.0% (2/5) |
| Conversation turn pass rate | 71.4% |
| Unexpected slot prompts | 0 |
| API errors (after retries) | 0 |
| Latency p50 / p95 / max | 395 ms / 559 ms / 1007 ms (n=68) |

## Per-intent precision / recall / F1

| Intent | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Default Fallback Intent | 5 | 0.50 | 1.00 | 0.67 |
| Default Welcome Intent | 4 | 1.00 | 0.75 | 0.86 |
| new.order | 4 | 1.00 | 0.75 | 0.86 |
| order.add | 20 | 0.95 | 0.95 | 0.95 |
| order.complete | 6 | 1.00 | 1.00 | 1.00 |
| order_remove | 5 | 1.00 | 0.80 | 0.89 |
| track.order | 6 | 1.00 | 0.67 | 0.80 |
| track.order - more | 4 | 1.00 | 1.00 | 1.00 |

## Confusion matrix

Rows = expected, columns = predicted.

| expected \ predicted | fallback | welcome | new.order | order.add | order.complete | order_remove | track.order | track.more |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fallback | **5** | . | . | . | . | . | . | . |
| welcome | 1 | **3** | . | . | . | . | . | . |
| new.order | 1 | . | **3** | . | . | . | . | . |
| order.add | 1 | . | . | **19** | . | . | . | . |
| order.complete | . | . | . | . | **6** | . | . | . |
| order_remove | . | . | . | 1 | . | **4** | . | . |
| track.order | 2 | . | . | . | . | . | **4** | . |
| track.more | . | . | . | . | . | . | . | **4** |

## Slot accuracy

`accuracy` is the extraction match on every case that labels the slot, regardless of the intent (a slot can be right under the wrong intent). `given intent` only counts cases where the intent was right, i.e. what the server would actually act on.

| Slot | Checked | Accuracy | Checked (intent right) | Accuracy given intent |
|---|---:|---:|---:|---:|
| food_Item | 27 | 96.3% | 25 | 100.0% |
| number | 25 | 68.0% | 22 | 77.3% |
| all labelled slots | 35 | 77.1% | | |

## By tag

| Tag | Cases | Intent accuracy | Pass rate |
|---|---:|---:|---:|
| a/an-quantity | 6 | 83.3% | 16.7% |
| chit-chat | 3 | 100.0% | 100.0% |
| missing-quantity | 3 | 100.0% | 100.0% |
| multi-item | 10 | 90.0% | 50.0% |
| no-context | 2 | 100.0% | 100.0% |
| numbers-as-words | 5 | 100.0% | 80.0% |
| off-menu | 2 | 100.0% | 100.0% |
| paraphrase | 11 | 90.9% | 90.9% |
| slang | 20 | 95.0% | 85.0% |
| synonym | 13 | 84.6% | 53.8% |
| track-id | 8 | 75.0% | 62.5% |
| typo | 11 | 81.8% | 72.7% |
| untagged | 3 | 100.0% | 100.0% |

## Conversations

| Conversation | Result | Turns passed | First failing turn |
|---|---|---:|---|
| ho-conv-01-full-order | FAIL | 3/4 | 2: "2 kotas n a malva pudding" (number [2, 1] -> [2]) |
| ho-conv-02-add-remove | FAIL | 2/3 | 2: "3 sosaties and a bobotie" (number [3, 1] -> [3]) |
| ho-conv-03-track-more | PASS | 2/2 |  |
| ho-conv-04-track-slot-fill | FAIL | 0/2 | 1: "is my order ready yet" (intent track.order -> new.order) |
| ho-conv-05-offmenu-then-add | PASS | 3/3 |  |

## Failures (11)

| Case | Text | Tags | What went wrong | Reply (truncated) |
|---|---|---|---|---|
| ho-welcome-03 | hie | typo | intent Default Welcome Intent -> Default Fallback Intent | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| ho-new-07 | ngicela ukuoda bru | slang | intent new.order -> Default Fallback Intent | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| ho-add-02 | a bunny n 2 vetkoeks pls | synonym, multi-item, a/an-quantity | number [1, 2] -> [2] | Got it, Bunny Chow and Vetkoek added. Would you like anything else? |
| ho-add-07 | eish bru i want 2 skhambane and a milk tart | slang, synonym, multi-item, a/an-quantity | number [2, 1] -> [2] | Added Kota and Milk Tart to your order. Anything else? |
| ho-add-10 | 1 bunny, 2 sosatie n a malva | synonym, multi-item, a/an-quantity, typo | number [1, 2, 1] -> [1, 2] | Added Bunny Chow, Sosaties and Malva Pudding to your order. Anything e |
| ho-add-13 | ill take a sphatlho | synonym, a/an-quantity | intent order.add -> Default Fallback Intent; food_Item ['Kota'] -> []; number [1] -> [] | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| ho-add-20 | yoh lemme get a boerie roll n 2 pap n chakalaka | slang, synonym, multi-item, a/an-quantity | number [1, 2] -> [2] | Got it, Boerewors Roll and Pap & Chakalaka added. Would you like anyth |
| ho-remove-05 | drop the boerie roll n the vetkoek | synonym, multi-item | intent order_remove -> order.add | Added Boerewors Roll and Vetkoek to your order. Anything else? |
| ho-track-04 | hows my food coming? no 512 | track-id, paraphrase | intent track.order -> Default Fallback Intent; number [512] -> [] | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| ho-track-05 | any news on ordr 9087 | track-id, typo | intent track.order -> Default Fallback Intent; number [9087] -> [] | Sorry, I didn't understand. You can say 'New order' or 'Track order'.  |
| ho-trackmore-04 | n my other one, number one hundred and twelve? | track-id, numbers-as-words | number [112] -> [1] | Checking the status of order #1. |

## Gate

- ok intent_accuracy: baseline 0.870 -> current 0.889 (drop -0.019, max 0.020)
- ok conversation_pass_rate: baseline 0.400 -> current 0.400 (drop +0.000, max 0.020)
