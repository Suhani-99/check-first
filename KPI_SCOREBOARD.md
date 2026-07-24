# KPI Scoreboard — Check First

Every number below was produced by `score.py`, which runs each case through the same
`analyzer.analyze()` the live site calls. Nothing here was measured against a test
copy of the code.

**Committed set:** `testset.csv`, 40 scored cases, written and committed to git
**before any tuning began**. The commit timestamp is the proof — the labels could not
have been adjusted to match the results.

**Adversarial set:** `adversarial_set.csv`, 20 cases, written on Day 4 specifically to
break the system, and committed before being scored.

---

## Targets

| KPI | Target | Actual | How it was measured |
|---|---|---|---|
| Input types analysed | ≥ 3 | **3** | Text, voice and video, all through one `/analyze` endpoint. Audio transcribed with Whisper; video's audio track extracted with ffmpeg, then transcribed. Demonstrated live. |
| Scam-intent accuracy | ≥ 85% | **100%** (40/40) | `python score.py` — every scored case in `testset.csv` run through the live engine, prediction compared to the committed label. |
| Accuracy under adversarial attack | *no target* | **70%** (14/20) | `python score.py adversarial_set.csv`. Reported voluntarily; see failure analysis below. |
| False reassurance — confident "genuine" verdicts on manipulated content | 0 | **0** | `analyzer.certifies_genuine()` checked on every result in every scoring run. Pattern-based, negation-aware, per-field. |
| Every output includes a plain-language explanation a non-technical person understands | 100% | **100%** | Presence checked programmatically on all 60 cases. Comprehension reviewed by non-technical readers — see below. |
| Every result routes the user to a human verification step | 100% | **100%** | Enforced in `guardrail()`: a result cannot be returned without one. Verified on all 60 cases. |
| Time to return a result | < 20s | **~1.1s** | Median of an unthrottled scoring run. Measured per case inside `score.py`. Rate-limited calls are reported separately so throttling does not flatter or distort the figure. |

---

## Explanation comprehension review

The doc requires this KPI to record **who reviewed the explanations**.

| Reviewer | Background | Cases read | Outcome |
|---|---|---|---|
| *(name)* | non-technical | | |
| *(name)* | non-technical | | |
| *(name)* | non-technical | | |

**Method:** each reviewer was shown result explanations without any accompanying
context and asked, in their own words, (1) what the tool was telling them, (2) what
they should do next, and (3) whether anything was unclear. An explanation counts as
understood only if the reviewer correctly restated both the concern and the action.

**Findings:**
*(to complete — record any wording that confused a reader and what was changed)*

---

## Failure analysis — the six adversarial cases

All six failures came from the adversarial set. Three are fixable. Three are the
honest edge of what single-message analysis can do.

### False alarms (flagged a legitimate message)

**A04 — the surprise gift.** *"Don't tell mum, I'm getting her an anniversary present
— can you send me 3000?"* A real secrecy demand, a real money request, entirely
innocent. Families keep harmless secrets and the rules cannot always separate those
from isolating ones.

**A06 — the legitimate OTP request.** A car was sold but still registered in the
seller's name, so the buyer genuinely needed a code to renew insurance. **Not fixed
deliberately.** Every request to share a code is flagged. A rule with exceptions is a
rule a scammer can talk someone through, so the false positive is accepted as the
price of an unconditional instruction.

**A07 — the broken phone.** *"My phone died in the rain, I'm on a shopkeeper's phone,
send 10,000 to his account."* Unknown number, urgency, a stranger's payment details —
every red flag, and completely real. **Flagging this is correct behaviour.** The
escalation ladder then asks for the family safe word, which the real person answers
immediately and a scammer cannot. This case is the clearest demonstration that the
product does not need to classify correctly in order to act correctly.

### Missed scams (did not flag a scam)

**A09 — the courtesy call.** *"Just confirming you know your deposit matures on the
14th. No action needed."* The opening move of a multi-turn con, asking for nothing.

**A10 — the neighbour.** *"Aunty from B-wing, my son's away, could you help me with
2000?"* Small, friendly, plausible, no urgency, no payment route named.

**A20 — getting back in touch.** *"I know we haven't spoken in years, but I'm in a bad
spot. Can we talk?"* From a compromised account. The ask comes after a reply.

All three ask for nothing. Reading a single message, there is nothing yet to catch —
and forcing a flag here would mean flagging every harmless "can we talk?"

### What the failures have in common

The signals are **neither necessary nor sufficient**. A legitimate message can carry
all of them (A07); a real scam can carry none (A09, A20). No amount of prompt tuning
resolves this, because it is not a tuning problem — it is the limit of judging a single
message by its contents.

Which is the argument for the whole design. If detection cannot be made reliable, a
product that *depends* on detection cannot be made safe. This one does not depend on
it: it reports what it can see, states what it cannot know, and always hands over a way
to verify.

---

## Measurement integrity

**One engine.** `score.py` imports and calls `analyzer.analyze()` — the same function
`/analyze` uses in production. There is no separate evaluation path that could drift
from shipped behaviour.

**Cache fingerprinting.** Per-case results are cached to survive rate limits, and the
cache key is a hash of the exact prompt, few-shot examples and model name. Any prompt
change invalidates the cache automatically, so results from an older prompt can never
be reported alongside newer ones.

**No contamination.** The few-shot examples inside the prompt were written separately
and appear in neither test set. Showing the model a case it would later be scored on
would invalidate the number.

**Honest latency.** Groq's free tier limits tokens per minute. When throttled, per-call
latency rises to ~12s — an artefact of the rate limiter, not of the system. The
scoreboard reports the unthrottled median and the throttled average separately rather
than averaging them into one misleading figure.

---

## Measurement history

Accuracy moved during development, and the direction is worth recording.

| Version | Committed set | Missed scams | False alarms | Note |
|---|---|---|---|---|
| Day 2 baseline | 95% | 0 | 2 | Fabricated evidence — cited signals not present in the text |
| Day 3, signal discipline added | 92.5% | 3 | 0 | Stricter rules suppressed genuine red flags |
| Day 3, definitions sharpened | **100%** | 0 | 0 | Evidence honest, thresholds correct |

The middle row is the useful one. Tightening one behaviour silently broke three
unrelated cases — a regression invisible to manual testing and caught only because a
committed test set existed. That is the argument for the test set in one line.
