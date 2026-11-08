import json
import os
import random
import re
import time
import urllib.request
from pathlib import Path
from curl_cffi import requests as cffi_requests

# --- 1A. ONE PIECE FILTER CONFIGURATION ---
OP_SET_PATTERN = re.compile(
    r"\b(op[- ]?(1[89]|2\d)|eb[- ]?0[5-9]|prb[- ]?0[3-9]|peb[- ]?0[1-9]|dominance of god|come in,? world|east blue)\b",
    re.IGNORECASE,
)
OP_REQUIRED = ["booster", "box", "display"]
OP_MIN_GBP = 80.00
OP_MAX_GBP = 130.00

# --- 1B. POKEMON FILTER CONFIGURATION (High-Value Sets + Future ME06-ME19 Codes) ---
PKMN_SET_PATTERN = re.compile(
    r"\b(me[- ]?(0[5-9]|1\d)|sv[- ]?(08\.5|09|10)|30th celebration|delta reign|flowing fists|pitch black|chaos rising|perfect order|ascended heroes|prismatic evolutions|destined rivals|surging sparks)\b",
    re.IGNORECASE,
)
PKMN_REQUIRED = ["booster box", "booster display", "elite trainer box", "etb", "pokemon center"]
PKMN_MIN_GBP = 38.00   # Catches £40-£55 ETBs while ignoring £20 tins/collections
PKMN_MAX_GBP = 145.00  # Catches £105-£135 Booster Boxes while ignoring scalped prices

# Shared exclusions for both games
EXCLUDED_KEYWORDS = [
    "sleeve", "binder", "case", "starter", "double pack", "double",
    "single", "blister", "japanese", "jp", "korean", "playmat", "deck",
    "empty", "damaged", "1x", "3x", "pack (", "gift", "tin", "illustration",
    "collection box", "ex box", "vstar", "figure", "plush", "mini tin"
]

# --- 2. VERIFIED HIGH-TRUST UK SHOPIFY STORES (8.2/10+ ONLY) ---
SHOPIFY_STORES = {
    "Beanie Games (9.8/10)": "https://beaniegames.co.uk",
    "Goblin Gaming - Northwich (9.7/10)": "https://www.goblingaming.co.uk",
    "JustPlay - Liverpool (9.5/10)": "https://justplaygames.uk",
    "Double Sleeved (9.4/10)": "https://www.doublesleeved.co.uk",
    "The Card Vault (9.3/10)": "https://thecardvault.co.uk",
    "Gathering Games (9.2/10)": "https://gatheringgames.co.uk",
    "Bulwark Games - Wirral (9.0/10)": "https://www.bulwarkgames.co.uk",
    "Obsidia TCG (8.8/10)": "https://obsidia-tcg.store",
    "Sports Cards Direct (8.5/10)": "https://www.sportscardsdirect.co.uk",
    "7th City Collectables (8.2/10)": "https://7thcitycollectables.com",
}

# --- 3. VERIFIED HIGH-TRUST WOOCOMMERCE STORES ---
WOO_STORES = {
    "Alchemists Workshops - Cheshire (9.1/10)": "https://alchemistsworkshops.com",
    "Game Summon (8.7/10)": "https://gamesummon.co.uk",
}

# --- 4. CUSTOM / BIGCOMMERCE GIANTS (Via Chrome TLS Impersonation) ---
CUSTOM_SEARCH_URLS = {
    "Chaos Cards (9.6/10)": "https://www.chaoscards.co.uk/shop/card-games/one-piece-card-game",
    "Magic Madhouse (9.5/10)": "https://magicmadhouse.co.uk/other-tcgs/one-piece-card-game",
    "Unicorn Cards (8.4/10)": "https://unicorncards.co.uk/collections/one-piece-booster-boxes",
}

STATE_FILE = Path("state.json")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def classify_match(product_title: str, variant_title: str, price: float) -> str | None:
    """Returns 'ONE PIECE' or 'POKEMON' if title & price match criteria, else None."""
    full_title = f"{product_title} {variant_title}".lower()
    if any(x in full_title for x in EXCLUDED_KEYWORDS):
        return None

    # 1. Check One Piece first
    if OP_SET_PATTERN.search(full_title) and any(k in full_title for k in OP_REQUIRED):
        if OP_MIN_GBP <= price <= OP_MAX_GBP:
            return "ONE PIECE"

    # 2. Check Pokemon in memory (0ms extra network cost)
    if PKMN_SET_PATTERN.search(full_title) and any(k in full_title for k in PKMN_REQUIRED):
        if PKMN_MIN_GBP <= price <= PKMN_MAX_GBP:
            return "POKEMON"

    return None


def send_alert(title: str, body: str, click_url: str):
    ntfy_topic = os.getenv("NTFY_TOPIC")
    if not ntfy_topic:
        print("[Alert Skipped]: NTFY_TOPIC secret is not set.")
        return

    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{ntfy_topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "max",
                "Tags": "rotating_light,shopping_cart",
                "Click": click_url,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[Alert Error - ntfy]: {e}")


def check_shopify_stores(state: dict) -> bool:
    changed = False
    for store_name, base_url in SHOPIFY_STORES.items():
        api_url = f"{base_url}/products.json?limit=250"
        try:
            resp = cffi_requests.get(api_url, impersonate="chrome124", timeout=15)
            if resp.status_code != 200:
                print(f"[{store_name}] HTTP {resp.status_code}")
                continue
            data = resp.json()
        except Exception as e:
            print(f"[{store_name}] Fetch skipped: {e}")
            continue

        for product in data.get("products", []):
            p_title = product.get("title", "")
            handle = product.get("handle", "")

            for variant in product.get("variants", []):
                v_id = str(variant.get("id"))
                v_title = variant.get("title", "")
                if v_title == "Default Title":
                    v_title = ""

                is_available = bool(variant.get("available", False))
                try:
                    price = float(variant.get("price", 999.99))
                except (ValueError, TypeError):
                    continue

                game_tag = classify_match(p_title, v_title, price)
                was_available = state.get(v_id, False)

                if is_available and game_tag:
                    if not was_available:
                        cart_1x = f"{base_url}/cart/{v_id}:1"
                        cart_2x = f"{base_url}/cart/{v_id}:2"
                        product_page = f"{base_url}/products/{handle}"

                        full_name = f"{p_title} ({v_title})".strip(" ()")
                        alert_title = f"[{game_tag}] {store_name} - £{price:.2f}"
                        alert_body = (
                            f"{full_name}\n"
                            f"1-Box Instant Cart: {cart_1x}\n"
                            f"2-Box Instant Cart: {cart_2x}\n"
                            f"Page: {product_page}"
                        )
                        print(f"[ALERT FIRED] {alert_title} | {full_name}")
                        send_alert(alert_title, alert_body, cart_1x)
                        state[v_id] = True
                        changed = True
                elif was_available:
                    print(f"[SOLD OUT / RESET] {store_name} | {p_title}")
                    state[v_id] = False
                    changed = True

        time.sleep(random.uniform(0.8, 1.8))
    return changed


def check_woo_stores(state: dict) -> bool:
    changed = False
    for store_name, base_url in WOO_STORES.items():
        api_url = f"{base_url}/wp-json/wc/store/v1/products?search=One+Piece&per_page=50"
        try:
            resp = cffi_requests.get(api_url, impersonate="chrome124", timeout=15)
            if resp.status_code != 200:
                print(f"[{store_name}] Woo HTTP {resp.status_code}")
                continue
            products = resp.json()
        except Exception as e:
            print(f"[{store_name}] Woo fetch skipped: {e}")
            continue

        for p in products:
            title = p.get("name", "")
            v_id = f"woo_{p.get('id')}"
            is_available = bool(p.get("is_in_stock", False) and p.get("is_purchasable", False))

            price_info = p.get("prices", {})
            minor_unit = int(price_info.get("currency_minor_unit", 2))
            try:
                price = float(price_info.get("price", "999999")) / (10 ** minor_unit)
            except (ValueError, TypeError):
                continue

            game_tag = classify_match(title, "", price)
            was_available = state.get(v_id, False)

            if is_available and game_tag:
                if not was_available:
                    cart_1x = f"{base_url}/?add-to-cart={p.get('id')}&quantity=1"
                    cart_2x = f"{base_url}/?add-to-cart={p.get('id')}&quantity=2"
                    alert_title = f"[{game_tag}] {store_name} - £{price:.2f}"
                    alert_body = (
                        f"{title}\n"
                        f"1-Box Instant Cart: {cart_1x}\n"
                        f"2-Box Instant Cart: {cart_2x}\n"
                        f"Page: {p.get('permalink')}"
                    )
                    print(f"[ALERT FIRED] {alert_title} | {title}")
                    send_alert(alert_title, alert_body, cart_1x)
                    state[v_id] = True
                    changed = True
            elif was_available:
                state[v_id] = False
                changed = True

        time.sleep(random.uniform(0.8, 1.8))
    return changed


def check_custom_stores(state: dict) -> bool:
    changed = False
    ld_json_pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )

    for store_name, url in CUSTOM_SEARCH_URLS.items():
        try:
            resp = cffi_requests.get(url, impersonate="chrome124", timeout=15)
            if resp.status_code != 200:
                print(f"[{store_name}] Skipped (HTTP {resp.status_code})")
                continue
            html = resp.text
        except Exception as e:
            print(f"[{store_name}] Fetch error: {e}")
            continue

        for match in ld_json_pattern.finditer(html):
            try:
                block = json.loads(match.group(1).strip())
            except Exception:
                continue

            items = []
            if isinstance(block, dict):
                if block.get("@type") == "Product":
                    items.append(block)
                elif block.get("@type") == "ItemList":
                    for el in block.get("itemListElement", []):
                        item = el.get("item", el)
                        if isinstance(item, dict):
                            items.append(item)

            for item in items:
                title = item.get("name", "")
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                avail_str = str(offers.get("availability", "")).lower()
                is_available = any(k in avail_str for k in ["instock", "preorder", "in_stock"])
                try:
                    price = float(offers.get("price", 999.99))
                except (ValueError, TypeError):
                    continue

                game_tag = classify_match(title, "", price)
                prod_url = offers.get("url") or item.get("url") or url
                v_id = f"custom_{store_name}_{re.sub(r'[^a-z0-9]', '', title.lower())}"
                was_available = state.get(v_id, False)

                if is_available and game_tag:
                    if not was_available:
                        alert_title = f"[{game_tag}] {store_name} - £{price:.2f}"
                        alert_body = f"{title}\nBuy Now: {prod_url}"
                        print(f"[ALERT FIRED] {alert_title} | {title}")
                        send_alert(alert_title, alert_body, prod_url)
                        state[v_id] = True
                        changed = True
                elif was_available:
                    state[v_id] = False
                    changed = True

        time.sleep(random.uniform(1.0, 2.0))
    return changed


def run_all_checks():
    if os.getenv("TEST_MODE") == "true":
        send_alert(
            "TEST: Dual Sniper Active (OP + PKMN)",
            "Scanning 15 UK Stores every 60s.\n1-Tap Test: https://www.bulwarkgames.co.uk",
            "https://www.bulwarkgames.co.uk",
        )
        print("Sent test alert successfully.")
        return

    start_time = time.time()
    loop_duration_sec = 28 * 60
    iteration = 1

    state = load_state()
    any_change_across_session = False

    print("=== Starting 25-Minute Continuous Loop (One Piece + Pokemon) ===")

    while (time.time() - start_time) < loop_duration_sec:
        print(f"\n--- [Sweep #{iteration}] Elapsed: {int(time.time() - start_time)}s ---")
        cycle_changed = False

        if check_shopify_stores(state):
            cycle_changed = True
        if check_woo_stores(state):
            cycle_changed = True
        if check_custom_stores(state):
            cycle_changed = True

        if cycle_changed:
            save_state(state)
            any_change_across_session = True

        iteration += 1
        time.sleep(random.uniform(55, 65))

    print("\n=== 25-Minute Session Complete. Passing off to next scheduled runner. ===")
    if any_change_across_session:
        save_state(state)


if __name__ == "__main__":
    run_all_checks()