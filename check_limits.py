"""
check_limits.py — shows exactly which Groq rate limit you are against.

Groq enforces FOUR separate limits and returns all of them in response headers:
  RPM  requests per minute
  RPD  requests per day
  TPM  tokens  per minute   <- long prompts hit this first
  TPD  tokens  per day      <- what stops a full scoring run

Run:  python check_limits.py
Costs one tiny request.
"""
import os
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"

resp = client.chat.completions.with_raw_response.create(
    model=MODEL,
    max_tokens=1,
    messages=[{"role": "user", "content": "hi"}],
)
h = resp.headers

rows = [
    ("Requests / minute", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests"),
    ("Tokens   / minute", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens"),
    ("Requests / day", "x-ratelimit-limit-requests-day", "x-ratelimit-remaining-requests-day", "x-ratelimit-reset-requests-day"),
    ("Tokens   / day", "x-ratelimit-limit-tokens-day", "x-ratelimit-remaining-tokens-day", "x-ratelimit-reset-tokens-day"),
]

print(f"\nModel: {MODEL}\n")
print(f"{'limit':<20}{'allowed':>12}{'remaining':>12}{'resets in':>14}")
print("-" * 58)
for label, lim, rem, res in rows:
    if h.get(lim) is None and h.get(rem) is None:
        continue
    print(f"{label:<20}{h.get(lim, '-'):>12}{h.get(rem, '-'):>12}{h.get(res, '-'):>14}")

print("\nAny other rate-limit headers returned:")
for k, v in h.items():
    if "ratelimit" in k.lower() and not any(k == c for _, a, b, c2 in rows for c in (a, b, c2)):
        print(f"  {k}: {v}")

print("\nTip: if TOKENS run out long before REQUESTS, your prompt is too long "
      "for the budget - shortening it buys you more runs.\n")