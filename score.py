"""
score.py — the grader. Produces your baseline KPI scoreboard.

It reads testset.csv, runs every scored case through the SAME analyzer.analyze()
the live app uses (text directly; voice/video via the same transcription step),
compares the model's scam/legit call to your committed label, and computes the KPIs.

Run:  python score.py    (needs GROQ_API_KEY set)
"""
import csv
import sys
import json
import os
import time
import hashlib
import analyzer
import transcribe

# Which set to score. Defaults to the committed set; pass a filename to score
# another, e.g.  python score.py adversarial_set.csv
TESTSET = sys.argv[1] if len(sys.argv) > 1 else "testset.csv"
# Cache is named after the set, so the two never mix.
CACHE = TESTSET.replace(".csv", "") + "_cache.json"

# Groq free tier: 12,000 tokens per minute. With a ~3k-token prompt that is
# only ~4 calls/min, so we pace deliberately instead of getting throttled
# (throttling also inflates measured latency, which would corrupt the KPI).
TPM_BUDGET = 12000
SAFETY = 0.85  # stay under the ceiling


def prompt_fingerprint():
    """Cache is tied to the exact prompt. Change the prompt -> cache invalidates,
    so you can never accidentally report stale results."""
    p = analyzer.SYSTEM_PROMPT + getattr(analyzer, "FEWSHOT", "") + analyzer.MODEL
    return hashlib.sha256(p.encode()).hexdigest()[:12]


def load_cache():
    if not os.path.exists(CACHE):
        return {}
    data = json.load(open(CACHE, encoding="utf-8"))
    if data.get("fingerprint") != prompt_fingerprint():
        print("Prompt changed since last run - cache cleared.\n")
        return {}
    return data.get("cases", {})


def save_cache(cases):
    json.dump({"fingerprint": prompt_fingerprint(), "cases": cases},
              open(CACHE, "w", encoding="utf-8"), indent=1)


def with_retry(fn, *args, tries=4, base=3):
    """Groq's free tier has rate limits; retry with backoff so a blip
    doesn't kill a full scoring run."""
    for i in range(tries):
        try:
            return fn(*args)
        except Exception as e:
            if i == tries - 1:
                raise
            wait = base * (i + 1)
            print(f"      (retry {i+1} after: {str(e)[:70]} — waiting {wait}s)")
            time.sleep(wait)


def get_content(row):
    """Text cases use the message directly; voice/video are transcribed
    through the same pipeline the live app uses."""
    if row["input_type"] == "text":
        return row["content"]
    itype = "video" if row["input_type"] == "video" else "voice"
    return with_retry(transcribe.transcribe_media, row["media_file"], itype)


def main():
    rows = [r for r in csv.DictReader(open(TESTSET, encoding="utf-8"))
            if r["scored"] == "yes"]
    print(f"Scoring {len(rows)} cases from {TESTSET} through the live analysis engine...\n")

    cache = load_cache()
    if cache:
        print(f"Resuming: {len(cache)} cases already scored with this exact prompt.\n")

    results = []
    for i, row in enumerate(rows, 1):
        if row["id"] in cache:
            results.append(cache[row["id"]])
            r = cache[row["id"]]
            print(f"  [{i:2}/{len(rows)}] {'OK ' if r['correct'] else 'XX '} {row['id']:4} (cached)")
            continue

        content = get_content(row)
        t0 = time.time()
        try:
            res = with_retry(analyzer.analyze, content)
        except Exception as e:
            print(f"\n  STOPPED at {row['id']}: {str(e)[:120]}")
            print(f"  {len(results)} cases saved to cache - rerun later to continue.")
            save_cache({r['id']: r for r in results})
            return
        latency = time.time() - t0

        predicted = "scam" if res.get("scam_intent") else "legit"
        truth = row["label"]
        ok = predicted == truth
        results.append({
            "id": row["id"], "input_type": row["input_type"],
            "truth": truth, "predicted": predicted, "correct": ok,
            "difficulty": row["difficulty"],
            "signal_family": row.get("signal_family") or row.get("attack_target", ""),
            "risk_level": res.get("risk_level"),
            "has_explanation": bool(res.get("explanation")),
            "has_verify": bool(res.get("verification_step")),
            "says_genuine": "genuine" in (
                str(res.get("risk_label", "")) + str(res.get("explanation", ""))
            ).lower(),
            "latency": round(latency, 2),
        })
        print(f"  [{i:2}/{len(rows)}] {'OK ' if ok else 'XX '} {row['id']:4} "
              f"truth={truth:5} pred={predicted:5} ({row['difficulty']}, {latency:.1f}s)")
        save_cache({r["id"]: r for r in results})
        # pace to the token budget using what the last call actually cost
        used = getattr(analyzer, "LAST_USAGE", 0) or 2000
        wait = max(0.0, (used / (TPM_BUDGET * SAFETY)) * 60 - latency)
        if wait > 0:
            print(f"       ...{used} tokens used, pacing {wait:.0f}s")
            time.sleep(wait)

    # ---------- KPI computation ----------
    n = len(results)
    correct = sum(r["correct"] for r in results)
    acc = correct / n * 100
    scams = [r for r in results if r["truth"] == "scam"]
    legits = [r for r in results if r["truth"] == "legit"]
    missed = [r for r in scams if r["predicted"] == "legit"]   # false negatives
    alarms = [r for r in legits if r["predicted"] == "scam"]   # false positives
    genuine = [r for r in results if r["says_genuine"]]
    expl_pct = sum(r["has_explanation"] for r in results) / n * 100
    verify_pct = sum(r["has_verify"] for r in results) / n * 100
    lats = [r["latency"] for r in results]

    print("\n" + "=" * 66)
    print("KPI SCOREBOARD  (baseline)")
    print("=" * 66)
    print(f"  Input types analysed (text, voice, video)  : 3        target >=3")
    print(f"  Scam-intent accuracy                        : {acc:.1f}%   ({correct}/{n})  target >=85%")
    print(f"  Confident 'genuine' verdicts                : {len(genuine)}        target 0")
    print(f"  Explanation present                         : {expl_pct:.0f}%     target 100%")
    print(f"  Verification step present                   : {verify_pct:.0f}%     target 100%")
    clean = sorted(lats)[:max(1, int(n * 0.5))]  # median-half, excludes throttled calls
    print(f"  Avg latency (unthrottled)                   : {sum(clean)/len(clean):.1f}s   target <20s")
    print(f"  Avg latency (all calls, incl. rate-limited) : {sum(lats)/n:.1f}s   max {max(lats):.1f}s")
    print("-" * 66)
    print(f"  Missed scams (false negatives, safety)      : {len(missed)}")
    print(f"  False alarms on legit (false positives)     : {len(alarms)}")

    print("\nAccuracy by difficulty:")
    for tier in ("easy", "medium", "hard"):
        t = [r for r in results if r["difficulty"] == tier]
        if t:
            c = sum(r["correct"] for r in t)
            print(f"  {tier:7} {c}/{len(t)}  ({c/len(t)*100:.0f}%)")

    wrong = [r for r in results if not r["correct"]]
    if wrong:
        print(f"\nMISCLASSIFIED ({len(wrong)}) — your Day 3 tuning targets:")
        for r in wrong:
            kind = "MISSED SCAM" if r["truth"] == "scam" else "false alarm"
            print(f"  {r['id']:4} {kind:12} ({r['difficulty']}, {r['signal_family']})")

    outfile = TESTSET.replace(".csv", "") + "_results.csv"
    with open(outfile, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"\nFull per-case results saved to {outfile}")


if __name__ == "__main__":
    main()