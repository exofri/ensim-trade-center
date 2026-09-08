import json, random, time

# A live, self-updating test feed -- exercises the real fetch_decision() code path
# in market_tick.py (URL fetch, JSON parsing) the same way a real student's repo
# would, without needing an actual second person. Writes fresh random values in
# [-1, 1] per product every time it runs.

PRODUCTS = ["A", "B", "C"]

def main():
    decision = {p: round(random.uniform(-1, 1), 3) for p in PRODUCTS}
    decision["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open("test_trader/decision.json", "w") as f:
        json.dump(decision, f, indent=2)
    print(f"Wrote test_trader/decision.json: {decision}")

if __name__ == "__main__":
    main()
