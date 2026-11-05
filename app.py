"""Simple web page for the hotel agent, so anyone on the home Wi-Fi can use it.

Run:  python app.py
Then open the link it prints, on this computer or on a phone on the same Wi-Fi.
"""

import os
import socket
from datetime import date

from flask import Flask, render_template_string, request

import hotel_agent as ha

app = Flask(__name__)

PORT = 8080  # not 5000, which macOS uses for AirPlay
DAILY_LIMIT = int(os.getenv("DAILY_SEARCH_LIMIT", "20"))  # protects the free SerpApi quota
MAX_SHOWN = 8
_usage = {"day": None, "count": 0}


class UserError(Exception):
    """A problem worth showing to the person using the page."""


def take_search_slot():
    today = date.today()
    if _usage["day"] != today:
        _usage["day"], _usage["count"] = today, 0
    if _usage["count"] >= DAILY_LIMIT:
        return False
    _usage["count"] += 1
    return True


def describe(params):
    """One line saying how the request was understood, so mistakes are easy to spot."""
    cur = params.get("currency", "CAD")
    parts = [
        params["location"],
        f"{params['check_in']} to {params['check_out']}",
        f"{params.get('adults', 2)} adults",
    ]
    if params.get("max_price_per_night"):
        parts.append(f"under {cur} {params['max_price_per_night']:.0f} a night")
    if params.get("min_rating"):
        parts.append(f"{params['min_rating']} stars or better")
    return " | ".join(parts)


def run_search(text):
    if not take_search_slot():
        raise UserError("Today's search limit was reached. Try again tomorrow.")
    try:
        params = ha.parse_request(text)
        if params["check_in"] < date.today().isoformat():
            raise UserError("That check-in date is in the past. Try dates from today onward.")
        nights = ha.nights_between(params["check_in"], params["check_out"])
        props = ha.fetch_hotels(params)
        results = ha.normalize(
            props, nights, params.get("max_price_per_night"), params.get("min_rating")
        )
    except UserError:
        raise
    except SystemExit as e:  # the command line helpers exit with a message
        raise UserError(str(e.code))
    except Exception:
        app.logger.exception("search failed")
        raise UserError("Something went wrong while searching. Please try again in a minute.")

    summary = ""
    if results:
        try:
            summary = ha.summarize(params, results)
        except Exception:
            app.logger.exception("summary failed")  # the list is still useful without it
    return params, results[:MAX_SHOWN], summary


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hotel Finder</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1c2430; --muted:#667085; --line:#e3e7ee; --accent:#1f6feb; --good:#067647; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12151a; --card:#1b2028; --text:#e8ecf2; --muted:#9aa4b2; --line:#2a313c; --accent:#6ea8ff; --good:#4ade80; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:16px/1.5 -apple-system, system-ui, "Segoe UI", sans-serif; }
  main { max-width:640px; margin:0 auto; padding:24px 16px 48px; }
  h1 { font-size:26px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 20px; }
  textarea { width:100%; min-height:84px; padding:12px; border:1px solid var(--line); border-radius:10px;
             background:var(--card); color:var(--text); font:inherit; resize:vertical; }
  button.go { margin-top:10px; width:100%; padding:13px; border:0; border-radius:10px; background:var(--accent);
              color:#fff; font:inherit; font-weight:600; cursor:pointer; }
  button.go:disabled { opacity:.6; cursor:wait; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 0; }
  .chip { border:1px solid var(--line); background:var(--card); color:var(--text); border-radius:999px;
          padding:6px 12px; font:inherit; font-size:14px; cursor:pointer; }
  .note, .error, .summary { border-radius:10px; padding:12px 14px; margin:18px 0 0; }
  .note { color:var(--muted); font-size:14px; padding:0; }
  .error { background:#fde8e8; color:#8a1c1c; }
  @media (prefers-color-scheme: dark) { .error { background:#3a1b1b; color:#ffb4b4; } }
  .summary { background:var(--card); border:1px solid var(--line); }
  .hotel { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin:12px 0; }
  .hotel .top { display:flex; justify-content:space-between; gap:12px; align-items:baseline; }
  .name { font-weight:600; }
  .total { font-weight:700; font-size:20px; white-space:nowrap; }
  .meta { color:var(--muted); font-size:14px; margin-top:2px; }
  .badge { color:var(--good); font-weight:600; }
  .hotel a { display:inline-block; margin-top:8px; color:var(--accent); text-decoration:none; font-size:14px; }
  footer { color:var(--muted); font-size:13px; margin-top:28px; }
</style>
</head>
<body>
<main>
  <h1>Hotel Finder</h1>
  <p class="sub">Say where, when and what you want. It finds the cheapest options.</p>

  <form method="get" action="/" onsubmit="var b=document.getElementById('go'); b.disabled=true; b.textContent='Searching... this takes about 10 seconds';">
    <textarea name="q" id="q" placeholder="Example: Victoria, Nov 20 to 23, under $150 a night, at least 3.5 stars" required>{{ q }}</textarea>
    <button class="go" id="go" type="submit">Find hotels</button>
  </form>

  <div class="chips">
    {% for ex in examples %}
      <button type="button" class="chip" onclick="document.getElementById('q').value=this.dataset.t; document.getElementById('q').focus();" data-t="{{ ex }}">{{ ex }}</button>
    {% endfor %}
  </div>

  {% if error %}<div class="error">{{ error }}</div>{% endif %}

  {% if searched and not error %}
    <p class="note">Searched for: {{ understood }}</p>
    {% if not results %}
      <div class="error">No hotels matched. Try a higher budget, a lower minimum rating, or different dates.</div>
    {% endif %}
    {% if summary %}<div class="summary">{{ summary }}</div>{% endif %}
    {% for h in results %}
      <div class="hotel">
        <div class="top">
          <span class="name">{{ loop.index }}. {{ h.name }}</span>
          <span class="total">{{ cur }} {{ "%.0f"|format(h.total) }}</span>
        </div>
        <div class="meta">
          {{ cur }} {{ "%.0f"|format(h.per_night) }} per night
          {% if h.rating %} | <span class="badge">{{ h.rating }} stars</span>{% else %} | no rating{% endif %}
        </div>
        {% if h.link %}<a href="{{ h.link }}" target="_blank" rel="noopener noreferrer">Open hotel website</a>{% endif %}
      </div>
    {% endfor %}
    {% if results %}
      <footer>Prices come from Google Hotels and may not include taxes and fees. Check the final price before booking.</footer>
    {% endif %}
  {% endif %}
</main>
</body>
</html>
"""

EXAMPLES = [
    "Victoria, Nov 20 to 23, under $150 a night",
    "Vancouver next weekend, at least 4 stars",
    "Kelowna Dec 5 to 7, 2 adults",
]


@app.route("/")
def home():
    q = request.args.get("q", "").strip()[:300]
    ctx = {"q": q, "examples": EXAMPLES, "searched": False, "error": "", "results": [],
           "summary": "", "understood": "", "cur": "CAD"}
    if q:
        ctx["searched"] = True
        try:
            params, results, summary = run_search(q)
            ctx.update(results=results, summary=summary, understood=describe(params),
                       cur=params.get("currency", "CAD"))
        except UserError as e:
            ctx["error"] = str(e)
    return render_template_string(PAGE, **ctx)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    print("\nHotel Finder is running.")
    print(f"  On this computer:        http://localhost:{PORT}")
    print(f"  On a phone (same Wi-Fi): http://{lan_ip()}:{PORT}")
    print(f"  Search limit: {DAILY_LIMIT} per day. Press Ctrl+C to stop.\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
