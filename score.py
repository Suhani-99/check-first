"""
score.py — the grader. Produces your baseline KPI scoreboard.

It reads testset.csv, runs every scored case through the SAME analyzer.analyze()
the live app uses (text directly; voice/video via the same transcription step),
compares the model's scam/legit call to your committed label, and computes the KPIs.

Run:  python score.py    (needs GROQ_API_KEY set)
"""
import csv
import time
import analyzer
import transcribe

TESTSET = "testset.csv"


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
    print(f"Scoring {len(rows)} cases through the live analysis engine...\n")

    results = []
    for i, row in enumerate(rows, 1):
        content = get_content(row)
        t0 = time.time()
        res = with_retry(analyzer.analyze, content)
        latency = time.time() - t0

        predicted = "scam" if res.get("scam_intent") else "legit"
        truth = row["label"]
        ok = predicted == truth
        results.append({
            "id": row["id"], "input_type": row["input_type"],
            "truth": truth, "predicted": predicted, "correct": ok,
            "difficulty": row["difficulty"], "signal_family": row["signal_family"],
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
        time.sleep(0.4)  # gentle on the free tier

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
    print(f"  Avg latency                                 : {sum(lats)/n:.1f}s   max {max(lats):.1f}s  target <20s")
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

    with open("scored_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print("\nFull per-case results saved to scored_results.csv")


if __name__ == "__main__":
    main()