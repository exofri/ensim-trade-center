import json, os, sys

STATUS_PATH = "market/status.json"
STATE_PATH = "market/state.json"
REGISTRY_PATH = "traders/registry.json"

def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def register_trader(trader_id, trader_name, decision_url):
    reg = load_json(REGISTRY_PATH, default={"traders": []})
    if any(t["id"] == trader_id for t in reg["traders"]):
        print(f"Trader '{trader_id}' already registered -- updating name/decision_url instead of duplicating.")
        for t in reg["traders"]:
            if t["id"] == trader_id:
                t["name"] = trader_name or t.get("name", trader_id)
                t["decision_url"] = decision_url
    else:
        reg["traders"].append({"id": trader_id, "name": trader_name or trader_id, "decision_url": decision_url})
    save_json(REGISTRY_PATH, reg)

    holdings_path = f"traders/{trader_id}/holdings.json"
    if not os.path.exists(holdings_path):
        # Product list can't come from market/config.json anymore (it's a Secret,
        # not readable here) -- ORGANIZER_PRODUCTS is a small, non-secret env var
        # set directly in the workflow file instead (Step 7), just the three
        # letters, not the trend formula.
        products = os.environ.get("ORGANIZER_PRODUCTS", "A,B,C").split(",")
        save_json(holdings_path, {"money": 1000.0, "products": {p: 1000.0 for p in products}})
        print(f"Registered '{trader_id}' ({trader_name}) with starting 1000 money and 1000 of each product.")
    else:
        print(f"Registered '{trader_id}' ({trader_name}) -- existing holdings left untouched.")

def override_price(product, new_price):
    state = load_json(STATE_PATH)
    if state is None or product not in state.get("prices", {}):
        print(f"ERROR: no existing state for product '{product}' -- run at least one market tick first.")
        sys.exit(1)
    old = state["prices"][product]
    state["prices"][product] = float(new_price)
    save_json(STATE_PATH, state)
    print(f"Overrode price of '{product}': {old} -> {new_price}. Takes effect on the next tick's blend.")

def adjust_holdings(trader_id, money_delta, product, product_delta):
    holdings_path = f"traders/{trader_id}/holdings.json"
    h = load_json(holdings_path)
    if h is None:
        print(f"ERROR: trader '{trader_id}' not found -- register them first.")
        sys.exit(1)
    h["money"] = max(0.0, h["money"] + float(money_delta))
    if product:
        if product not in h["products"]:
            print(f"ERROR: unknown product '{product}'.")
            sys.exit(1)
        h["products"][product] = max(0.0, h["products"][product] + float(product_delta))
    save_json(holdings_path, h)
    print(f"Adjusted '{trader_id}': money_delta={money_delta}, {product or '(none)'}_delta={product_delta}. New: {h}")

def set_paused(paused):
    save_json(STATUS_PATH, {"paused": paused})
    print(f"Market paused = {paused}.")

def main():
    action = os.environ["ACTION"]
    if action == "register_trader":
        register_trader(os.environ["TRADER_ID"], os.environ.get("TRADER_NAME", ""), os.environ["DECISION_URL"])
    elif action == "override_price":
        override_price(os.environ["PRODUCT"], os.environ["NEW_PRICE"])
    elif action == "adjust_holdings":
        adjust_holdings(os.environ["TRADER_ID"], os.environ.get("MONEY_DELTA", "0"),
                         os.environ.get("PRODUCT", ""), os.environ.get("PRODUCT_DELTA", "0"))
    elif action == "pause":
        set_paused(True)
    elif action == "resume":
        set_paused(False)
    else:
        print(f"ERROR: unknown action '{action}'.")
        sys.exit(1)

if __name__ == "__main__":
    main()
