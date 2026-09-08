import json, math, os, random, time
import requests

STATUS_PATH = "market/status.json"
STATE_PATH = "market/state.json"
HISTORY_PATH = "market/price_history.csv"
REGISTRY_PATH = "traders/registry.json"

# Built-in reference traders from the original brief -- not real students, no
# decision_url to fetch. Generated fresh each tick from a fixed distribution, so
# every real trader has a consistent behavioral baseline to compare against
# (per project2_ensim_trade_center.md's "Behavior relative to baselines" KPI).
SYNTHETIC_TRADERS = {
    "random_trader": lambda rng: rng.uniform(-1, 1),
    "random_buyer": lambda rng: rng.uniform(0, 1),
    "random_seller": lambda rng: rng.uniform(-1, 0),
}

def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)

def trend_price(t, cfg):
    base, amp, period, phase = cfg["base"], cfg["amplitude"], cfg["period_ticks"], cfg["phase"]
    drift = cfg.get("drift_per_tick", 0.0)
    return (base + drift * t) * (1 + amp * math.sin(2 * math.pi * t / period + phase))

def fetch_decision(url, retries=2, backoff=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, proxies={"http": None, "https": None}, timeout=(10, 20))
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(backoff)
    print(f"    could not fetch decision from {url} ({last_error}) -- treating as hold (0) this tick")
    return None

def clean_decision(raw, products):
    # A trader's file is untrusted input from outside this repo -- validate every
    # field defensively. Missing/malformed/out-of-range values become 0 (hold) for
    # that specific product only, never a crash for the whole tick.
    out = {}
    for p in products:
        v = raw.get(p) if raw else None
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        out[p] = max(-1.0, min(1.0, v))
    return out

def main():
    # The trend/cycle formula and its sensitivity parameters are secret -- loaded
    # from the MARKET_CONFIG_JSON environment variable (a GitHub Secret at runtime,
    # Step 5's workflow), never a committed file. Nothing in the public repo lets a
    # trader read the deterministic trend directly instead of inferring it from
    # observed prices.
    cfg = json.loads(os.environ["MARKET_CONFIG_JSON"])

    status = load_json(STATUS_PATH, default={"paused": False})
    if status.get("paused"):
        print("Market is paused (market/status.json 'paused': true) -- skipping this tick.")
        return

    products = list(cfg["products"].keys())
    state = load_json(STATE_PATH, default={"t": 0, "prices": {p: cfg["products"][p]["base"] for p in products}})
    t = state["t"] + 1
    rng = random.Random()

    registry = load_json(REGISTRY_PATH, default={"traders": []})["traders"]
    print(f"Tick {t}: fetching decisions for {len(registry)} registered traders "
          f"+ {len(SYNTHETIC_TRADERS)} built-in baselines...")
    decisions_by_trader = {}
    for entry in registry:
        raw = fetch_decision(entry["decision_url"])
        decisions_by_trader[entry["id"]] = clean_decision(raw, products)

    # Built-in baseline traders participate exactly like real ones from here on:
    # same demand aggregation, same trade execution, same leaderboard -- just no
    # HTTP fetch, their "decision" is generated directly from a fixed distribution.
    for name, dist in SYNTHETIC_TRADERS.items():
        decisions_by_trader[name] = {p: dist(rng) for p in products}

    all_trader_ids = list(decisions_by_trader.keys())
    # Display names for the leaderboard -- registry entries carry "name" (Step 8's
    # bulk-registration format); baseline traders just use their own id as a name.
    names_by_id = {entry["id"]: entry.get("name", entry["id"]) for entry in registry}
    for name in SYNTHETIC_TRADERS:
        names_by_id.setdefault(name, name)

    # Demand signal per product: AVERAGE across all traders (real + baseline), not
    # sum -- bounded to [-1,1] regardless of class size, so price sensitivity
    # doesn't need re-tuning every time a trader is added or removed.
    avg_demand = {}
    for p in products:
        vals = [decisions_by_trader[tid][p] for tid in decisions_by_trader]
        avg_demand[p] = sum(vals) / len(vals) if vals else 0.0

    new_prices = {}
    for p in products:
        pcfg = cfg["products"][p]
        T = trend_price(t, pcfg)
        M = state["prices"][p] * (1 + cfg["demand_sensitivity"] * avg_demand[p])
        price = 0.5 * T + 0.5 * M
        price *= (1 + random.gauss(0, cfg["noise_std"]))
        new_prices[p] = max(price, 0.01)

    print(f"  new prices: {[(p, round(new_prices[p], 3)) for p in products]}")
    print(f"  avg demand: {[(p, round(avg_demand[p], 3)) for p in products]}")

    # Execute each trader's implied trade at this tick's new price, against their
    # own authoritative holdings ledger in traders/<id>/holdings.json (baseline
    # traders get one too, same as real ones). Executed in a fixed product order
    # (A, B, C, ...) -- a real simplification worth knowing: if a trader's money
    # runs out mid-tick, later products in the order get whatever's left, not a
    # proportional share. Clipped defensively either way, never negative.
    for tid in all_trader_ids:
        decisions = decisions_by_trader[tid]
        holdings_path = f"traders/{tid}/holdings.json"
        h = load_json(holdings_path, default={"money": 1000.0, "products": {p: 1000.0 for p in products}})
        for p in products:
            d = decisions[p]
            price = new_prices[p]
            if d > 0:
                desired = 100 * d
                affordable = h["money"] / price if price > 0 else 0
                units = min(desired, affordable)
                h["money"] -= units * price
                h["products"][p] += units
            elif d < 0:
                desired = 100 * abs(d)
                units = min(desired, h["products"][p])
                h["money"] += units * price
                h["products"][p] -= units
            h["money"] = max(h["money"], 0.0)
            h["products"][p] = max(h["products"][p], 0.0)
        os.makedirs(f"traders/{tid}", exist_ok=True)
        with open(holdings_path, "w") as f:
            json.dump(h, f, indent=2)

    # Append to history, persist state
    header_needed = not os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a") as f:
        if header_needed:
            f.write("t,timestamp," + ",".join(f"price_{p}" for p in products) + "\n")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        f.write(f"{t},{ts}," + ",".join(str(round(new_prices[p], 4)) for p in products) + "\n")

    with open(STATE_PATH, "w") as f:
        json.dump({"t": t, "prices": new_prices}, f, indent=2)

    # Public leaderboard: rank + name + total_value ONLY -- deliberately no money
    # or per-product breakdown, so traders can't see each other's actual positions
    # (see the "deliberately hidden" note near the top of this manual for the real
    # limits of that privacy). "baseline": true flags the three built-in reference
    # traders so the dashboard can visually separate them from real students.
    leaderboard = []
    for tid in all_trader_ids:
        h = load_json(f"traders/{tid}/holdings.json", default={"money": 0.0, "products": {p: 0.0 for p in products}})
        total_value = h["money"] + sum(h["products"][p] * new_prices[p] for p in products)
        leaderboard.append({"id": tid, "name": names_by_id.get(tid, tid),
                             "total_value": round(total_value, 2),
                             "baseline": tid in SYNTHETIC_TRADERS})
    leaderboard.sort(key=lambda r: r["total_value"], reverse=True)

    # Cap the chart export to the most recent 500 ticks -- price_history.csv itself
    # keeps the full record for anyone who wants to pull more.
    with open(HISTORY_PATH) as f:
        rows = f.read().strip().split("\n")[1:]
    recent = rows[-500:]
    chart_points = []
    for row in recent:
        parts = row.split(",")
        chart_points.append({"t": int(parts[0]), "timestamp": parts[1],
                              **{p: float(parts[2 + i]) for i, p in enumerate(products)}})

    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/market.json", "w") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "products": products, "history": chart_points}, f, indent=2)
    with open("docs/data/leaderboard.json", "w") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "leaderboard": leaderboard}, f, indent=2)

    print(f"Tick {t} complete. State, history, and dashboard data updated.")

if __name__ == "__main__":
    main()
