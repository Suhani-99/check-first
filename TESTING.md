# TESTING — Check First

How the evaluation sets were built, how to reproduce every number, and what the
results actually mean.

Results summary: [KPI_SCOREBOARD.md](KPI_SCOREBOARD.md) · Architecture: [DESIGN.md](DESIGN.md)

---

## Reproducing everything

```bash
export GROQ_API_KEY="gsk_..."

python check_limits.py                # check remaining rate-limit budget first
python score.py                       # committed set  -> 100% (40 cases)
python score.py adversarial_set.csv   # adversarial set -> 70%  (20 cases)
```

Each run writes `<set>_results.csv` with a per-case row, and caches progress in
`<set>_cache.json`. A rate limit mid-run is not fatal — rerun and it resumes.

**A full run takes ~10 minutes** on the free tier, because the harness paces itself to
12,000 tokens/minute. Running it faster produces throttled calls and a corrupted
latency figure.

---

## Set 1 — the committed set (`testset.csv`)

**40 scored cases: 20 scam, 20 legitimate.** Committed to git before any tuning began.

### Design principles

**Balanced.** An unbalanced set rewards a lazy classifier. With 20/20, a system that
flags everything scores 50%, so the number cannot be gamed by over-flagging.

**Difficulty tiered.** 12 easy, 18 medium, 10 hard. Real ambiguity lives in the middle,
so the set is weighted there rather than at the extremes.

**Built as twins.** Nearly every legitimate case is the deliberate mirror of a scam,
sharing its surface features:

| Scam | Legitimate twin | What it forces the model to learn |
|---|---|---|
| S01/S19 emergency, money, secrecy | L02 real pharmacy emergency | urgency + money ≠ scam |
| S03 fake bank asking for an OTP | L03 real bank alert mentioning OTP | who is asking, not the topic |
| S04 fake disconnection threat | L16 real utility bill | a threat with a deadline vs a due date |
| S11 vendor payment redirect | L06 routine vendor payment | *changed* details, not payment itself |
| S20 fake delivery panic link | L04 real shipping notice | a link is not a signal |
| S07 lottery advance fee | L10 real cashback | reward ≠ prize scam |
| S17 crypto "guaranteed returns" | L20 registered SIP proposal | investment ≠ investment scam |

Without twins, a model can score well by keying on surface features — "mentions
money", "has a deadline", "is a bank". Twins make those features useless and force it
to find the discriminating signal.

**Coverage.** Emergency impersonation, authority/credential theft, payment redirect
(BEC), prize and job advance-fee, threat/extortion, romance and investment, phishing
links, account takeover.

### Voice cases

10 of the 40 are audio. Legitimate ones are real recordings; scam ones are synthetic
speech.

**Why synthetic for the scam side:** a real attacker clones the *relative's* voice, not
the user's. A synthetic voice saying scam content is the accurate threat model. It also
demonstrates the point that matters — detection runs on transcribed content, so voice
identity does not affect the verdict.

### Video cases

10 unscored cases prove the third input type end to end. Manipulated clips were made by
replacing a video's audio track with synthetic scam speech, producing genuine
audio–visual mismatch. Video is demonstrated live but excluded from the scored accuracy
figure, because a handful of cases cannot support a meaningful percentage.

---

## Set 2 — the adversarial set (`adversarial_set.csv`)

**20 cases, written on Day 4 to break the system.** Committed before being scored.

### Why it exists

The committed set reached 100%. A set scoring full marks has stopped being informative —
it can no longer tell whether a change helped or hurt. Worse, manual testing had already
found a failure the set could not see: *"mom i need help pls send 5000 rn"* was flagged
as a scam, and no case in the 40 was that terse.

That is the generalisation illusion appearing in our own system — strong benchmark
performance, degradation on a real-world distribution the benchmark did not represent.
The adversarial set attacks the assumptions rather than sampling the distribution.

### What each case attacks

| Attack | Cases | Assumption under test |
|---|---|---|
| Terseness | A01–A03 | that real messages carry context |
| Legitimate secrecy | A04 | that secrecy implies scam |
| Legitimate OTP request | A06 | that any code request is fraud |
| Every-signal-legitimate | A07 | that red flags imply danger |
| Signal-free scams | A08, A10, A20 | that scams announce themselves |
| Multi-turn opener | A09 | that a scam's first move is detectable |
| Hinglish | A11–A13 | that the prompt reads code-mixed text |
| Paraphrase | A14, A15 | that the model learned patterns, not phrasings |
| Fake vs real alerts | A18, A19 | that bank-alert format implies legitimacy |

**Three cases came from real experience** rather than invention — collected from family
members who receive these calls regularly:

- **A06** — a car sold but still registered in the seller's name, so the buyer
  legitimately needed an OTP to renew insurance.
- **A07** — a phone destroyed in the rain, calling from a stranger's handset, needing
  money sent to that stranger's UPI.
- **A09** — the "courtesy call" opener, where the caller already knows your deposit
  maturity date because the data was leaked or sold.

---

## Results

| Set | Cases | Accuracy | Missed scams | False alarms |
|---|---|---|---|---|
| Committed | 40 | **100%** | 0 | 0 |
| Adversarial | 20 | **70%** | 3 | 3 |

By difficulty, adversarial: easy 2/2, medium 8/8, **hard 4/10**. Every failure is in the
tier built to break it — the boundary is exactly where it was designed to be.

Full per-case output: `testset_results.csv`, `adversarial_set_results.csv`.
Failure analysis: [KPI_SCOREBOARD.md](KPI_SCOREBOARD.md#failure-analysis--the-six-adversarial-cases).

---

## Guardrail tests

`analyzer.certifies_genuine()` is unit-tested against phrasings that must be caught and
phrasings that must not be:

**Must catch:** "this appears to be a genuine birthday wish" · "the sender is
legitimate" · "it seems like a real message" · "it is safe to send the money" · "this
is not a scam, you can send it"

**Must not flag:** "this tool never confirms a message is genuine" (our own disclaimer)
· "we cannot confirm whether this is genuine" · "ask them something only the real
person would know" · "no signs of scam intent"

The second list is the harder half. An earlier exact-string implementation flagged the
product's own safety disclaimer while missing *"appears to **be a** genuine"* — the
intervening words defeated it. The current implementation is pattern-based,
negation-aware, and evaluated per field and per clause. See
[DESIGN.md §5](DESIGN.md#5-the-guardrail).

---

## Measurement integrity

**No contamination.** The three few-shot examples inside the prompt were written
separately and appear in neither set. Scoring a model on an example it was shown would
invalidate the result.

**Committed before tuned.** `testset.csv` was committed on Day 2, before the prompt was
tuned at all. Git timestamps are the proof.

**Adversarial set treated as held-out.** It was scored once, before any tuning against
it. Tuning on it and re-reporting would convert a held-out probe into a fitted number.

**Latency reported honestly.** Throttled calls (~12s) are an artefact of the free tier's
token limit, not system performance. The scoreboard reports the unthrottled median
(~1.1s) and the throttled average separately rather than blending them.

---

## What is not covered by automated tests

Stated because the gap is real.

**Follow-up guidance quality.** The chat replies are reviewed manually, not scored. A
weakness found by hand — the model suggesting the user *search online* for a police
number, which is how people reach SEO-poisoned fake helplines — would not have been
caught by any automated set. Building an evaluation set for conversational guidance is
the clearest next piece of work.

**Explanation comprehension** is measured by human review, not by script. See
[KPI_SCOREBOARD.md](KPI_SCOREBOARD.md#explanation-comprehension-review).

**Transcription accuracy** is assumed rather than measured. Spot checks showed clean
transcripts on the recorded cases, but no word-error-rate benchmark was run.
