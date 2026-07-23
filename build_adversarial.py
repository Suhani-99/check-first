# Builds adversarial_set.csv - cases designed to BREAK the system, not represent it.
#
# Sources:
#   (a) real fraud patterns reported by family members who receive these calls
#   (b) deliberate attacks on assumptions encoded in the current prompt
#
# This set is SEPARATE from the committed testset.csv, which stays frozen.
# It is expected to score LOWER. That is the point.
import csv

FIELDS = ["id", "input_type", "content", "label", "scored", "difficulty",
          "attack_target", "media_file", "note"]

rows = [
# ---------- TERSE (the known coverage gap) ----------
["A01", "text", "mom send 5000 rn", "legit", "yes", "hard", "terseness", "",
 "Minimal real request. No secrecy, no unknown route, no impersonation. Terse is how people actually text."],
["A02", "text", "mom new number, send 20k to this upi urgently 8823xxxx@ybl", "scam", "yes", "medium", "terseness", "",
 "Equally terse but carries real flags: new number + unfamiliar UPI. Tests that brevity itself is not the signal."],
["A03", "text", "papa emergency need money", "legit", "yes", "hard", "terseness", "",
 "Four words, no detail at all. Must not flag on urgency alone."],

# ---------- LEGIT MESSAGES CARRYING SCAM SIGNALS ----------
["A04", "text", "Don't tell mum, I'm getting her a surprise gift for the anniversary. Can you send me 3000? I'll pay you back Friday.", "legit", "yes", "hard", "secrecy=legit", "",
 "Genuine secrecy demand + money + deadline. Families keep innocent secrets. Directly attacks the rule 'secrecy implies scam'."],
["A05", "text", "Got a random OTP from Zomato that I didn't ask for, is someone trying to get into my account?", "legit", "yes", "medium", "otp-mention", "",
 "Mentions an OTP but is ASKING about one, not requesting one. Tests direction of the request."],
["A06", "text", "Hi, this is Rajan who bought your Alto. Insurance renewal needs the OTP on your phone since the RC is still in your name - same as last time. Call me if you'd rather do it together.", "legit", "yes", "hard", "otp-legit", "",
 "REAL CASE from family: car sold, registration still in seller's name, buyer legitimately needs the OTP. Known person, expected transaction, invites verification. Attacks 'OTP request always means scam'."],
["A07", "text", "Ma, phone bheeg gaya in the rain and it's dead. I'm calling from a shopkeeper's phone. Send 10000 to his UPI and he'll give me the cash - I have no way to pay for anything.", "legit", "yes", "hard", "every-signal-legit", "",
 "REAL SCENARIO: unknown number + urgency + money + a STRANGER'S UPI - every red flag, and genuinely legitimate. The hardest case in the set. Flagging is acceptable IF the safe-word rung resolves it."],

# ---------- SCAMS WITH ALMOST NO EXPLICIT SIGNALS ----------
["A08", "text", "Ma, I can't explain right now, please just trust me and send whatever you can. I'll tell you everything later.", "scam", "yes", "hard", "no-explicit-signal", "",
 "Pure emotional pressure. 'Can't explain / later' blocks verification softly. No payment route, no secrecy word, no impersonation named."],
["A09", "text", "Good afternoon ma'am. I'm calling from your bank regarding the fixed deposit maturing on the 14th - just a courtesy check that you're aware of the maturity date. No action needed today.", "scam", "yes", "hard", "multi-turn-opener", "",
 "TURN 1 of a conversational scam. Asks for nothing. The tell is unexplained knowledge of private finances - a real bank does not cold-call to recite what it already holds. Tests whether the system can flag a groomer's opening move."],
["A10", "text", "Beta, I saw your number on the society group. Aunty here from B-wing. My son is out of station and I'm stuck, could you help me with 2000? I'll return it tomorrow when he's back.", "scam", "yes", "hard", "no-explicit-signal", "",
 "Social-proximity scam. Friendly, plausible, small amount, no urgency words, no payment route named."],

# ---------- HINGLISH / CODE-MIXED (zero coverage in the committed set) ----------
["A11", "text", "Mummy main bol raha hun, naya number hai. Urgent 15000 chahiye, papa ko mat batana.", "scam", "yes", "medium", "hinglish", "",
 "New number + urgency + secrecy, all in Hinglish. Tests whether an English-tuned prompt reads code-mixed text."],
["A12", "text", "Beta ghar aate waqt doodh le aana, aur papa ko phone kar dena.", "legit", "yes", "easy", "hinglish", "",
 "Ordinary Hinglish family message. Must not flag."],
["A13", "text", "Sir aapka KYC pending hai, account block ho jayega aaj raat. Turant is number pe call kijiye 9821xxxxxx", "scam", "yes", "medium", "hinglish", "",
 "Hinglish authority scam: impersonation + threat + fake deadline + unverified number."],

# ---------- PARAPHRASE ROBUSTNESS ----------
["A14", "text", "Hi Mom - long story but there was an accident and the hospital needs a deposit before they'll treat me. Around forty thousand. Please keep this between us for now, I'll explain properly once I'm out.", "scam", "yes", "medium", "paraphrase", "",
 "S01 fully reworded: no 'UPI', no 'don't tell Papa', different structure. Tests whether the model learned the pattern or the phrasing."],
["A15", "text", "Ma'am, we've noticed unusual activity and need to secure your account. For security I'll need to confirm the code that's just been sent to your registered mobile.", "scam", "yes", "medium", "paraphrase", "",
 "S03 reworded: never says 'OTP', never names the bank, no explicit deadline."],

# ---------- MISCELLANEOUS PRESSURE POINTS ----------
["A16", "text", "hey it's me, phone died, on my friend's phone. call me here when you can", "legit", "yes", "medium", "new-number-legit", "",
 "New/unknown number but asks for NOTHING and invites a callback. Tests that an unknown number alone is not a flag."],
["A17", "text", "Your parcel could not be delivered. Reschedule here: https://bit.ly/3xKq9Lm", "scam", "yes", "easy", "shortlink", "",
 "Shortened link hides the real destination. Tests link handling when the domain is not visibly fake."],
["A18", "text", "Transaction of Rs 1,00,000 debited from a/c XX4432. If not you call 18001234567 immediately.", "scam", "yes", "hard", "fake-alert", "",
 "Fake debit alert with a scary amount and a number IN the message - the panic makes people dial it. Looks almost identical to a real bank SMS; the tell is that a real alert points to the number on your card, not one in the text."],
["A19", "text", "Dear customer, Rs 1,999 debited towards annual card fee as per your card terms. No action required.", "legit", "yes", "medium", "fake-alert", "",
 "Real fee-debit notice: no callback number, no urgency, no action requested. Innocent twin of A18."],
["A20", "text", "I know we haven't spoken in years but I'm in a really bad spot and you're the only person I could think of. I don't want to get into details over text. Can we talk?", "scam", "yes", "hard", "no-explicit-signal", "",
 "Re-engagement opener - hacked or spoofed account. No money mentioned at all; the ask comes after you reply."],
]

with open("adversarial_set.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(FIELDS)
    w.writerows(rows)

print(f"adversarial cases: {len(rows)}")
print(f"  scam : {sum(1 for r in rows if r[3]=='scam')}")
print(f"  legit: {sum(1 for r in rows if r[3]=='legit')}")
print("\nby attack target:")
targets = {}
for r in rows:
    targets[r[6]] = targets.get(r[6], 0) + 1
for t, c in sorted(targets.items(), key=lambda x: -x[1]):
    print(f"  {t:22} {c}")