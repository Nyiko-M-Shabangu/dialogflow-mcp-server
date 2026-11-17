# NLU eval report: baseline-2026-11-17

Agent `your-gcp-project-id` (Dialogflow ES, language `en`, draft). Run at 2026-11-17 13:31:07 UTC, 243 API calls, concurrency 4. Golden set fingerprint `cbe18b9d45516abf`.

Re-scored offline from stored observations (`evals/reports/baseline-2026-11-17/results.json`); latency is from that run.

## Headline

| Metric | Value |
|---|---|
| Single-turn cases | 182 |
| Intent accuracy | 73.6% |
| Macro F1 (intents) | 0.62 |
| Case pass rate (intent + slots + prompt) | 69.2% |
| Without the 11 cases verbatim in training (171 cases): intent accuracy / macro F1 / case pass rate | 76.6% / 0.62 / 71.9% |
| food_Item accuracy | 99.0% (99/100) |
| number accuracy | 81.8% (72/88) |
| Conversation pass rate | 56.2% (9/16) |
| Conversation turn pass rate | 79.6% |
| Unexpected slot prompts | 11 |
| API errors (after retries) | 0 |
| Latency p50 / p95 / max | 377 ms / 483 ms / 752 ms (n=236) |

## Per-intent precision / recall / F1

| Intent | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Default Fallback Intent | 20 | 0.44 | 0.90 | 0.59 |
| Default Welcome Intent | 10 | n/a | 0.00 | n/a |
| new.order | 18 | 1.00 | 0.22 | 0.36 |
| order.add | 72 | 0.94 | 0.85 | 0.89 |
| order.complete | 16 | 1.00 | 0.75 | 0.86 |
| order_remove | 18 | 0.57 | 0.89 | 0.70 |
| track.order | 20 | 0.67 | 0.90 | 0.77 |
| track.order - more | 8 | 1.00 | 0.62 | 0.77 |

## Confusion matrix

Rows = expected, columns = predicted.

| expected \ predicted | fallback | welcome | new.order | order.add | order.complete | order_remove | track.order | track.more |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fallback | **18** | . | . | 2 | . | . | . | . |
| welcome | 10 | . | . | . | . | . | . | . |
| new.order | 8 | . | **4** | . | . | . | 6 | . |
| order.add | 1 | . | . | **61** | . | 10 | . | . |
| order.complete | 2 | . | . | . | **12** | 2 | . | . |
| order_remove | . | . | . | 2 | . | **16** | . | . |
| track.order | 2 | . | . | . | . | . | **18** | . |
| track.more | . | . | . | . | . | . | 3 | **5** |

## Slot accuracy

`accuracy` is the extraction match on every case that labels the slot, regardless of the intent (a slot can be right under the wrong intent). `given intent` only counts cases where the intent was right, i.e. what the server would actually act on.

| Slot | Checked | Accuracy | Checked (intent right) | Accuracy given intent |
|---|---:|---:|---:|---:|
| food_Item | 100 | 99.0% | 85 | 100.0% |
| number | 88 | 81.8% | 77 | 89.6% |
| all labelled slots | 121 | 86.8% | | |

## By tag

| Tag | Cases | Intent accuracy | Pass rate |
|---|---:|---:|---:|
| a/an-quantity | 12 | 41.7% | 0.0% |
| chit-chat | 19 | 47.4% | 47.4% |
| exact-phrase | 4 | 100.0% | 100.0% |
| missing-quantity | 5 | 40.0% | 40.0% |
| multi-item | 16 | 93.8% | 68.8% |
| no-context | 1 | 100.0% | 100.0% |
| numbers-as-words | 15 | 100.0% | 93.3% |
| off-menu | 12 | 83.3% | 75.0% |
| paraphrase | 66 | 60.6% | 60.6% |
| slang | 25 | 72.0% | 68.0% |
| synonym | 16 | 87.5% | 81.2% |
| track-id | 21 | 85.7% | 76.2% |
| typo | 21 | 71.4% | 71.4% |
| untagged | 14 | 92.9% | 92.9% |

## Conversations

| Conversation | Result | Turns passed | First failing turn |
|---|---|---:|---|
| conv-01-happy-path | FAIL | 2/3 | 2: "2 bunny chows and a milk tart" (number [2, 1] -> [2]) |
| conv-02-add-add-remove-complete | PASS | 5/5 |  |
| conv-03-track-with-id | PASS | 1/1 |  |
| conv-04-track-without-id | PASS | 2/2 |  |
| conv-05-track-then-how-about | PASS | 2/2 |  |
| conv-06-track-prompt-then-follow-up | PASS | 3/3 |  |
| conv-07-off-menu-mid-order | FAIL | 3/4 | 2: "a pizza please" (intent Default Fallback Intent -> order_remove; empty reply) |
| conv-08-restart | PASS | 5/5 |  |
| conv-09-missing-quantity-slot-fill | FAIL | 2/4 | 2: "I want bunny chow" (intent order.add -> order_remove) |
| conv-10-greet-then-order | FAIL | 0/4 | 1: "hi" (intent Default Welcome Intent -> Default Fallback Intent) |
| conv-11-remove-before-order | FAIL | 2/3 | 3: "a kota" (intent order.add -> order_remove; number [1] -> []) |
| conv-12-done-before-order-then-track | PASS | 2/2 |  |
| conv-13-slang-order | FAIL | 3/4 | 4: "dats it" (intent order.complete -> Default Fallback Intent) |
| conv-14-chit-chat-mid-order | PASS | 4/4 |  |
| conv-15-track-then-order | FAIL | 3/4 | 3: "a bunny chow" (intent order.add -> order_remove; number [1] -> []) |
| conv-16-numbers-as-words | PASS | 4/4 |  |

## Failures (56)

| Case | Text | Tags | What went wrong | Reply (truncated) |
|---|---|---|---|---|
| wel-01 | hi | chit-chat | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-02 | hello | chit-chat | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-03 | hey there | chit-chat, paraphrase | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-04 | howzit | chit-chat, slang | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-05 | sho | chit-chat, slang | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-06 | sawubona | chit-chat, slang | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-07 | good morning | chit-chat, paraphrase | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-08 | good evening! | chit-chat, paraphrase | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-09 | heita | chit-chat, slang | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| wel-10 | helo | chit-chat, typo | intent Default Welcome Intent -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-03 | I want to place an order | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-04 | I'd like to order | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-05 | I would like to order | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-06 | can I order some food | paraphrase | intent new.order -> track.order; unexpected slot prompt | Definitely. What is your order id? |
| new-08 | let's order | paraphrase | intent new.order -> track.order; unexpected slot prompt | Sure. Please enter your order id. |
| new-09 | I'm hungry, I want to order | paraphrase | intent new.order -> track.order; unexpected slot prompt | Definitely. What is your order id? |
| new-10 | place an order | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-11 | can i place an oder | typo | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-13 | lemme order sumthing | slang, typo | intent new.order -> track.order; unexpected slot prompt | Definitely. What is your order id? |
| new-14 | I want to buy food | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-15 | order food | paraphrase | intent new.order -> track.order; unexpected slot prompt | Sure. Please enter your order id. |
| new-16 | make an order | paraphrase | intent new.order -> track.order; unexpected slot prompt | Sure. Please enter your order id. |
| new-17 | hey, I want to order food | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| new-18 | start order | paraphrase | intent new.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| add-21 | a kota | a/an-quantity | intent order.add -> order_remove; number [1] -> [] | I have removed Kota |
| add-22 | I'd like a bunny chow | a/an-quantity | intent order.add -> order_remove; number [1] -> [] | I have removed Bunny Chow |
| add-23 | a malva pudding please | a/an-quantity | intent order.add -> order_remove; number [1] -> [] | I have removed Malva Pudding |
| add-24 | can I get a boerewors roll | a/an-quantity | number [1] -> []; unexpected slot prompt | Please specify how much you want. |
| add-25 | a kota and 2 bunny chows | a/an-quantity, multi-item | number [1, 2] -> [2] | Added Kota and Bunny Chow to your order. Anything else? |
| add-26 | a milk tart and a vetkoek | a/an-quantity, multi-item | number [1, 1] -> []; unexpected slot prompt | Please specify how much you want. |
| add-27 | an order of bobotie | a/an-quantity, paraphrase | intent order.add -> order_remove; number [1] -> [] | I have removed Bobotie |
| add-28 | throw in a malva pudding | a/an-quantity, paraphrase | intent order.add -> order_remove; number [1] -> [] | I have removed Malva Pudding |
| add-33 | a quarter bunny | slang, synonym, a/an-quantity | number [1] -> [0.25] | Added Bunny Chow to your order. Anything else? |
| add-38 | a curry bunny | synonym, a/an-quantity | intent order.add -> order_remove; number [1] -> [] | I have removed Bunny Chow |
| add-44 | 3 vetcook | typo | intent order.add -> Default Fallback Intent; food_Item ['Vetkoek'] -> []; number [3] -> [] | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| add-49 | a milktart | typo, synonym, a/an-quantity | intent order.add -> order_remove; number [1] -> [] | I have removed Milk Tart |
| add-57 | 2 kotas plus a bunny chow | multi-item, a/an-quantity | number [2, 1] -> [2] | Got it, Kota and Bunny Chow added. Would you like anything else? |
| add-66 | I want bunny chow | missing-quantity | intent order.add -> order_remove | I have removed Bunny Chow |
| add-68 | some vetkoek please | missing-quantity | intent order.add -> order_remove | I have removed Vetkoek |
| add-69 | bobotie | missing-quantity | intent order.add -> order_remove | I have removed Bobotie |
| add-72 | 1 bobotie and 2 burgers | off-menu, multi-item | number [1] -> [1, 2] | Got it, Bobotie added. Would you like anything else? |
| rem-11 | drop the milk tarts and the kota | multi-item, paraphrase | intent order_remove -> order.add; unexpected slot prompt | Please specify how much you want. |
| rem-15 | can you remove 1 kota |  | intent order_remove -> order.add | Added Kota to your order. Anything else? |
| cmp-04 | no thanks | paraphrase | intent order.complete -> order_remove |  |
| cmp-06 | that will be all | paraphrase | intent order.complete -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| cmp-08 | checkout please | paraphrase | intent order.complete -> order_remove |  |
| cmp-13 | dats it | slang, typo | intent order.complete -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| trk-08 | track order one thousand and five | track-id, numbers-as-words | number [1005] -> [1000] | Got it. Checking the status of order #1000. |
| trk-18 | where's my food? | paraphrase, slang | intent track.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| trk-20 | how long until my food arrives | paraphrase | intent track.order -> Default Fallback Intent | I didn't understand. You can say 'New order' or 'Track order'. In an o |
| trm-02 | what about order 1500 | track-id, paraphrase | intent track.order - more -> track.order | Got it. Checking the status of order #1500. |
| trm-04 | check 3003 too | track-id, paraphrase | intent track.order - more -> track.order | Got it. Checking the status of order #3003. |
| trm-05 | also order 42 | track-id, paraphrase | intent track.order - more -> track.order | Got it. Checking the status of order #42. |
| trm-06 | how about order number 999 | track-id | number [999] -> [] |  |
| fbo-06 | give me 2 biryani | off-menu | intent Default Fallback Intent -> order.add; unexpected slot prompt | Please specify what you want to eat |
| fbo-09 | add 2 cheeseburgers | off-menu | intent Default Fallback Intent -> order.add; unexpected slot prompt | Please specify what you want to eat |

## Notes

- First baseline. Labels describe what a good product should do, so failures are product gaps, not harness noise.
- Most order cases inject the ongoing-order / ongoing-tracking input context through QueryParameters (1 API call per case); 7 cases send a real 'new order' turn first to check that both setups behave the same.
- At the time of this run the live agent's reply texts differed from the export in dialogflow/agent/; the agent was re-exported before the training-phrase fixes, and the export in this repository is current.
- Re-scored offline on 2026-11-17 after cases verbatim in the training phrases were labelled (verbatim_in_training); observations, latency and API calls are from the original run.
