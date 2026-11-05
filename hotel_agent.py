"""Hotel price agent.

Plain English request in, cheapest hotels out.

Usage:
    python hotel_agent.py "Victoria BC, Nov 20 to 23, under $150 a night"
    python hotel_agent.py            (prompts you for a request)
    python hotel_agent.py "..." --top 10 --no-summary
"""

import argparse
import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

SERPAPI_URL = "https://serpapi.com/search.json"
MODEL = "claude-haiku-5-5"  # cheapest tier, plenty for parsing and summarizing
LOCAL_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Vancouver"))


def today():
    """Today's date where we live, even when the server runs on UTC."""
    return datetime.now(LOCAL_TZ).date()

SEARCH_TOOL = {
    "name": "search_hotels",
    "description": "Search hotels for a stay.",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or area, for example 'Victoria, BC'",
            },
            "check_in": {"type": "string", "description": "YYYY-MM-DD"},
            "check_out": {"type": "string", "description": "YYYY-MM-DD"},
            "adults": {"type": "integer", "description": "Number of adults"},
            "max_price_per_night": {
                "type": "number",
                "description": "Maximum nightly price, only if the user gave one",
            },
            "currency": {"type": "string", "description": "3 letter code, e.g. CAD"},
            "min_rating": {
                "type": "number",
                "description": "Minimum guest rating out of 5, only if the user asked for one",
            },
        },
        "required": ["location", "check_in", "check_out"],
    },
}


def parse_request(text):
    """Use Claude to turn free text into structured search parameters."""
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=(
            f"Today is {today().isoformat()}. Convert the user's hotel request "
            "into a search_hotels call. If a date has no year, use the next future "
            "occurrence. Default to 2 adults and CAD unless told otherwise."
        ),
        tools=[SEARCH_TOOL],
        tool_choice={"type": "tool", "name": "search_hotels"},
        messages=[{"role": "user", "content": text}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    sys.exit("Could not understand the request. Try adding a city and dates.")


def nights_between(check_in, check_out):
    nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    if nights < 1:
        sys.exit("Check-out must be after check-in.")
    return nights


def fetch_hotels(p):
    """Call SerpApi Google Hotels. Returns the raw list of properties."""
    key = os.getenv("SERPAPI_KEY")
    if not key:
        sys.exit("Missing SERPAPI_KEY. Add it to your .env file.")

    params = {
        "engine": "google_hotels",
        "q": f"{p['location']} hotels",
        "check_in_date": p["check_in"],
        "check_out_date": p["check_out"],
        "adults": p.get("adults", 2),
        "currency": p.get("currency", "CAD"),
        "gl": "ca",
        "hl": "en",
        "sort_by": 3,  # 3 = lowest price
        "api_key": key,
    }
    if p.get("max_price_per_night"):
        params["max_price"] = int(p["max_price_per_night"])

    r = requests.get(SERPAPI_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        sys.exit(f"SerpApi error: {data['error']}")
    return data.get("properties", [])


def normalize(properties, nights, max_per_night=None, min_rating=None):
    """Keep only what we need and sort by total price, cheapest first."""
    results = []
    for h in properties:
        per_night = (h.get("rate_per_night") or {}).get("extracted_lowest")
        total = (h.get("total_rate") or {}).get("extracted_lowest")
        if total is None and per_night is not None:
            total = per_night * nights
        if total is None:
            continue
        if per_night is None:
            per_night = total / nights
        if max_per_night and per_night > max_per_night:
            continue
        rating = h.get("overall_rating")
        if min_rating and (rating is None or rating < min_rating):
            continue
        results.append(
            {
                "name": h.get("name", "Unknown"),
                "per_night": per_night,
                "total": total,
                "rating": h.get("overall_rating"),
                "link": h.get("link", ""),
            }
        )
    return sorted(results, key=lambda x: x["total"])


def summarize(request, results):
    """Short Claude summary of the top results. Only uses the data given."""
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=(
            "Summarize hotel search results in 3 short sentences. Use only the data "
            "given. Name the cheapest option and one good value pick. Do not invent "
            "details."
        ),
        messages=[
            {
                "role": "user",
                "content": json.dumps({"request": request, "results": results[:5]}),
            }
        ],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def show(results, top, currency):
    for i, h in enumerate(results[:top], 1):
        rating = h["rating"] if h["rating"] else "n/a"
        print(f"{i}. {h['name']}")
        print(
            f"   {currency} {h['total']:.0f} total "
            f"({currency} {h['per_night']:.0f}/night) | rating {rating}"
        )
        if h["link"]:
            print(f"   {h['link']}")


def main():
    parser = argparse.ArgumentParser(description="Cheapest hotel search agent")
    parser.add_argument("request", nargs="*", help="what you want, in plain English")
    parser.add_argument("--top", type=int, default=5, help="how many results to show")
    parser.add_argument("--no-summary", action="store_true", help="skip the Claude summary")
    args = parser.parse_args()

    text = " ".join(args.request).strip() or input("What hotel are you looking for? ")
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("Missing ANTHROPIC_API_KEY. Add it to your .env file.")

    params = parse_request(text)
    print(f"Searching: {json.dumps(params)}\n")

    nights = nights_between(params["check_in"], params["check_out"])
    props = fetch_hotels(params)
    results = normalize(props, nights, params.get("max_price_per_night"), params.get("min_rating"))

    if not results:
        sys.exit("No hotels found. Try different dates or a higher budget.")

    show(results, args.top, params.get("currency", "CAD"))

    if not args.no_summary:
        print("\nSummary:")
        print(summarize(params, results))


if __name__ == "__main__":
    main()
