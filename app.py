"""Hotel Finder: a simple web page for the hotel agent.

Run:  python app.py
Then open the link it prints, on this computer or on a phone on the same Wi-Fi.
"""

import hashlib
import hmac
import os
import socket
import time
from datetime import timedelta

from flask import Flask, redirect, render_template_string, request, session, url_for

import hotel_agent as ha

app = Flask(__name__)

PORT = 8080  # not 5000, which macOS uses for AirPlay
DAILY_LIMIT = int(os.getenv("DAILY_SEARCH_LIMIT", "20"))  # protects the free SerpApi quota
MAX_SHOWN = 8
_usage = {"day": None, "count": 0}

# Password protection: set APP_PASSWORD when the page is online, so strangers can't use up your searches.
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
_secret = os.getenv("SECRET_KEY")
if not _secret and APP_PASSWORD:
    _secret = hashlib.sha256(("hotel-finder-session:" + APP_PASSWORD).encode()).hexdigest()
app.secret_key = _secret or os.urandom(32)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("VERCEL") or os.getenv("RENDER")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
MAX_FAILS, FAIL_WINDOW = 8, 15 * 60
_fails = {}


class UserError(Exception):
    """A problem worth showing to the person using the page."""


def take_search_slot():
    today = ha.today()
    if _usage["day"] != today:
        _usage["day"], _usage["count"] = today, 0
    if _usage["count"] >= DAILY_LIMIT:
        return False
    _usage["count"] += 1
    return True


def fmt_dates(check_in, check_out):
    d1, d2 = ha.date.fromisoformat(check_in), ha.date.fromisoformat(check_out)
    left = f"{d1.strftime('%b')} {d1.day}"
    same_month = (d1.year, d1.month) == (d2.year, d2.month)
    right = str(d2.day) if same_month else f"{d2.strftime('%b')} {d2.day}"
    year = f", {d1.year}" if d1.year != ha.today().year else ""
    return f"{left} to {right}{year}"


def pills(params, nights):
    """Short pieces showing how the request was understood, so mistakes are easy to spot."""
    cur = params.get("currency", "CAD")
    adults = params.get("adults", 2)
    items = [
        params["location"],
        f"{fmt_dates(params['check_in'], params['check_out'])} ({nights} night{'s' if nights != 1 else ''})",
        f"{adults} adult{'s' if adults != 1 else ''}",
    ]
    if params.get("max_price_per_night"):
        items.append(f"Under {cur} {params['max_price_per_night']:.0f} a night")
    if params.get("min_rating"):
        items.append(f"{params['min_rating']:g}+ stars")
    return items


def decorate(results):
    """Add what the page needs: extra cost vs the cheapest, and which one is top rated."""
    if not results:
        return []
    base = results[0]["total"]
    rated = [h for h in results if h.get("rating")]
    top = max(rated, key=lambda h: h["rating"]) if len(results) > 1 and rated else None
    out = []
    for i, h in enumerate(results):
        d = dict(h)
        d["extra"] = h["total"] - base
        d["cheapest"] = i == 0
        d["top"] = top is not None and h is top
        out.append(d)
    return out


def run_search(text):
    if not take_search_slot():
        raise UserError("Today's search limit is used up. It resets tomorrow.")
    try:
        params = ha.parse_request(text)
        if params["check_in"] < ha.today().isoformat():
            raise UserError("That check-in date has already passed. Pick dates from today onward.")
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
        raise UserError("The search didn't go through. Wait a minute and try again.")

    summary = ""
    if results:
        try:
            summary = ha.summarize(params, results)
        except Exception:
            app.logger.exception("summary failed")  # the list is still useful without it
    return params, nights, results[:MAX_SHOWN], summary


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hotel Finder</title>
<meta name="theme-color" content="#EEF2F3" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0A1820" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#EEF2F3; --surface:#FFFFFF; --text:#0F2A36; --muted:#55707B; --line:#D5DEE2;
    --tide:#1C6E7D; --on-tide:#FFFFFF; --glass:#D3ECE6; --on-glass:#0F4A43; --buoy:#F2A93B;
    --ticket:#0F2A36; --on-ticket:#F3F8F9; --ticket-muted:#A9C3CB; --alert:#FBEBDD; --on-alert:#7A3B0A;
    --display:"Bricolage Grotesque", "Avenir Next", system-ui, sans-serif;
    --body:"Albert Sans", -apple-system, system-ui, "Segoe UI", sans-serif;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0A1820; --surface:#12262F; --text:#E6EEF0; --muted:#8FA8B1; --line:#22404B;
      --tide:#5CC0CE; --on-tide:#06222A; --glass:#1C3F3A; --on-glass:#BFE9DF;
      --ticket:#1D4352; --on-ticket:#F3F8F9; --ticket-muted:#A9C3CB; --alert:#3A2614; --on-alert:#F7C99A;
    }
  }
  * { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; }
  body { margin:0; background:var(--bg); color:var(--text); font:16px/1.55 var(--body); }
  .wrap { max-width:720px; margin:0 auto; padding:0 16px; }
  a { color:inherit; }
  :focus-visible { outline:3px solid var(--tide); outline-offset:3px; border-radius:8px; }
  .sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }

  .bar { padding:18px 16px 0; display:flex; justify-content:space-between; align-items:center; }
  .signout { font-size:14px; color:var(--muted); text-decoration:none; }
  .signout:hover { color:var(--text); }
  .brand { display:inline-flex; align-items:center; gap:8px; text-decoration:none;
           font:600 17px/1 var(--display); letter-spacing:-0.01em; }
  .brand svg { color:var(--tide); }

  .hero { padding:36px 0 8px; }
  h1 { font:800 clamp(34px, 8.6vw, 56px)/1.02 var(--display); letter-spacing:-0.03em; margin:0; max-width:14ch; }
  .lede { color:var(--muted); font-size:17px; margin:14px 0 24px; max-width:46ch; }

  .ask { background:var(--surface); border:1px solid var(--line); border-radius:20px; padding:8px; }
  textarea { display:block; width:100%; min-height:92px; border:0; background:transparent; color:var(--text);
             font:500 18px/1.45 var(--body); padding:12px 12px 6px; resize:none; }
  textarea::placeholder { color:var(--muted); opacity:.7; }
  textarea:focus-visible, .ask input:focus-visible { outline:none; }
  .ask input { display:block; width:100%; border:0; background:transparent; color:var(--text);
               font:500 18px/1.4 var(--body); padding:14px 12px; }
  .ask input::placeholder { color:var(--muted); opacity:.7; }
  .login-error { margin-top:14px; }
  .ask:focus-within { border-color:var(--tide); box-shadow:0 0 0 3px color-mix(in srgb, var(--tide) 22%, transparent); }
  .go { display:block; width:100%; min-height:52px; border:0; border-radius:14px; background:var(--tide);
        color:var(--on-tide); font:600 17px/1 var(--body); cursor:pointer; }
  .go:disabled { opacity:.7; cursor:progress; }

  .try { list-style:none; display:flex; flex-wrap:wrap; gap:8px; padding:0; margin:14px 0 0; }
  .try button { border:1px solid var(--line); background:transparent; color:var(--text); border-radius:999px;
                padding:7px 13px; font:500 14px/1.2 var(--body); cursor:pointer; text-align:left; }
  .try button:hover { border-color:var(--tide); }

  .results { padding:28px 0 0; }
  .understood { list-style:none; display:flex; flex-wrap:wrap; gap:6px; padding:0; margin:0 0 14px; }
  .understood li { background:var(--glass); color:var(--on-glass); border-radius:8px; padding:4px 10px;
                   font:500 14px/1.4 var(--body); }
  .summary { margin:0 0 20px; font-size:17px; max-width:60ch; }

  .list { list-style:none; padding:0; margin:0; display:grid; gap:10px; }
  .card { display:grid; grid-template-columns:1fr auto; text-decoration:none; border-radius:16px; overflow:hidden; }
  .card .info { padding:16px 16px 16px 18px; min-width:0; }
  .card .price { padding:16px 18px; text-align:right; display:flex; flex-direction:column; justify-content:center; }
  .name { font:600 17px/1.3 var(--body); margin:0; overflow-wrap:anywhere; }
  .meta { margin:4px 0 0; font-size:14px; color:var(--muted); display:flex; flex-wrap:wrap; gap:4px 12px; align-items:center; }
  .stars { display:inline-flex; align-items:center; gap:4px; }
  .stars svg { width:14px; height:14px; }
  .visit { font-size:14px; font-weight:600; color:var(--tide); margin-top:8px; display:inline-block; }
  .amount { font:700 26px/1 var(--display); letter-spacing:-0.02em; white-space:nowrap; }
  .amount small { font:600 13px/1 var(--body); letter-spacing:0; margin-right:3px; }
  .per { font-size:13px; color:var(--muted); margin-top:5px; white-space:nowrap; }
  .tags { display:flex; gap:6px; margin:0 0 6px; }
  .tag { font:600 12px/1 var(--body); padding:5px 8px; border-radius:6px; background:var(--glass); color:var(--on-glass); }

  .row { background:var(--surface); border:1px solid var(--line); }
  a.row:hover { border-color:var(--tide); }

  .ticket { background:var(--ticket); color:var(--on-ticket); }
  .ticket .meta, .ticket .per { color:var(--ticket-muted); }
  .ticket .visit { color:var(--buoy); }
  .ticket .price { position:relative; border-left:2px dashed color-mix(in srgb, var(--on-ticket) 30%, transparent); min-width:128px; }
  .ticket .price::before, .ticket .price::after { content:""; position:absolute; left:-10px; width:18px; height:18px;
                                                  border-radius:50%; background:var(--bg); }
  .ticket .price::before { top:-9px; }
  .ticket .price::after { bottom:-9px; }
  .ticket .amount { color:var(--buoy); font-size:34px; }
  .ticket .tag.cheap { background:var(--buoy); color:#0F2A36; }

  .notice { background:var(--alert); color:var(--on-alert); border-radius:14px; padding:14px 16px; margin:0; font-weight:500; }
  .fine { color:var(--muted); font-size:13px; margin:22px 0 40px; max-width:60ch; }

  .skel { height:82px; border-radius:16px; background:var(--surface); border:1px solid var(--line); position:relative; overflow:hidden; }
  .skel:first-child { height:104px; background:var(--ticket); border:0; }
  .skel::after { content:""; position:absolute; inset:0; transform:translateX(-100%);
                 background:linear-gradient(90deg, transparent, color-mix(in srgb, var(--tide) 14%, transparent), transparent);
                 animation:sweep 1.3s ease-in-out infinite; }
  .waiting { color:var(--muted); margin:0 0 12px; }
  @keyframes sweep { to { transform:translateX(100%); } }
  @media (prefers-reduced-motion: reduce) { .skel::after { animation:none; } }

  @media (min-width:640px) {
    .hero { padding-top:64px; }
    .ask { display:grid; grid-template-columns:1fr auto; align-items:end; gap:8px; }
    .go { width:auto; padding:0 26px; }
  }
</style>
</head>
<body>
<header class="bar wrap">
  <a class="brand" href="/">
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="1.5" width="14" height="17" rx="4" fill="currentColor"/><circle cx="10" cy="6" r="1.8" fill="var(--bg)"/></svg>
    Hotel Finder
  </a>
  {% if show_signout %}<a class="signout" href="/logout">Sign out</a>{% endif %}
</header>

<main class="wrap">
  {% if login %}
  <section class="hero">
    <h1>Find the cheapest place to stay</h1>
    <p class="lede">This page is private. Enter the password to start searching.</p>
    <form method="post" action="/login">
      <div class="ask">
        <label for="pw" class="sr-only">Password</label>
        <input type="password" name="password" id="pw" required autocomplete="current-password" placeholder="Password" autofocus>
        <button class="go" type="submit">Sign in</button>
      </div>
    </form>
    {% if error %}<p class="notice login-error" role="alert">{{ error }}</p>{% endif %}
  </section>
  {% else %}
  <section class="hero">
    <h1>Find the cheapest place to stay</h1>
    <p class="lede">Write the city, dates and budget the way you'd say them. It checks Google Hotels and sorts by what you'd pay for the whole stay.</p>

    <form method="get" action="/" id="form">
      <div class="ask">
        <label for="q" class="sr-only">Describe your trip</label>
        <textarea name="q" id="q" required maxlength="300"
          placeholder="For example: Kelowna, Dec 5 to 7, under $180 a night">{{ q }}</textarea>
        <button class="go" id="go" type="submit">Find hotels</button>
      </div>
    </form>

    {% if not searched %}
    <ul class="try" aria-label="Example searches">
      {% for ex in examples %}
        <li><button type="button" data-t="{{ ex }}">{{ ex }}</button></li>
      {% endfor %}
    </ul>
    {% endif %}
  </section>

  <section class="results" id="results" aria-live="polite">
    {% if error %}
      <p class="notice" role="alert">{{ error }}</p>
    {% elif searched %}
      <ul class="understood" aria-label="What was searched">
        {% for p in understood %}<li>{{ p }}</li>{% endfor %}
      </ul>

      {% if not results %}
        <p class="notice">No hotels matched. Raise the budget, lower the minimum rating, or try other dates.</p>
      {% else %}
        {% if summary %}<p class="summary">{{ summary }}</p>{% endif %}
        <ol class="list">
        {% for h in results %}
          <li>
            {% if h.link %}<a class="card {{ 'ticket' if h.cheapest else 'row' }}" href="{{ h.link }}" target="_blank" rel="noopener noreferrer">
            {% else %}<div class="card {{ 'ticket' if h.cheapest else 'row' }}">{% endif %}
              <div class="info">
                {% if h.cheapest or h.top %}
                <div class="tags">
                  {% if h.cheapest %}<span class="tag cheap">Cheapest</span>{% endif %}
                  {% if h.top %}<span class="tag">Top rated</span>{% endif %}
                </div>
                {% endif %}
                <p class="name">{{ h.name }}</p>
                <p class="meta">
                  {% if h.rating %}
                  <span class="stars"><svg viewBox="0 0 20 20" aria-hidden="true"><path fill="currentColor" d="M10 1.8l2.5 5.2 5.7.8-4.1 4 1 5.6L10 14.7l-5.1 2.7 1-5.6-4.1-4 5.7-.8z"/></svg>{{ h.rating }} rating</span>
                  {% else %}<span>No rating yet</span>{% endif %}
                  {% if not h.cheapest and h.extra >= 1 %}<span>{{ cur }} {{ "%.0f"|format(h.extra) }} more than the cheapest</span>{% endif %}
                </p>
                {% if h.link %}<span class="visit">Open hotel website</span>{% endif %}
              </div>
              <div class="price">
                <div class="amount"><small>{{ cur }}</small>{{ "%.0f"|format(h.total) }}</div>
                <div class="per">{{ cur }} {{ "%.0f"|format(h.per_night) }} a night</div>
              </div>
            {% if h.link %}</a>{% else %}</div>{% endif %}
          </li>
        {% endfor %}
        </ol>
        <p class="fine">Totals are for {{ nights }} night{{ 's' if nights != 1 }}. Prices come from Google Hotels and may not include taxes and fees, so check the final price on the hotel's site before booking.</p>
      {% endif %}
    {% endif %}
  </section>
  {% endif %}
</main>

{% if not login %}
<script>
  var form = document.getElementById('form'), go = document.getElementById('go'), q = document.getElementById('q');
  document.querySelectorAll('.try button').forEach(function (b) {
    b.addEventListener('click', function () { q.value = b.dataset.t; q.focus(); });
  });
  q.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (q.value.trim()) form.requestSubmit(); }
  });
  form.addEventListener('submit', function () {
    go.disabled = true; go.textContent = 'Checking prices';
    var r = document.getElementById('results');
    r.setAttribute('aria-busy', 'true');
    r.innerHTML = '<p class="waiting">Checking prices. This takes about 10 seconds.</p><div class="list"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>';
  });
  window.addEventListener('pageshow', function () { go.disabled = false; go.textContent = 'Find hotels'; });
</script>
{% endif %}
</body>
</html>
"""

EXAMPLES = [
    "Victoria, Nov 20 to 23, under $150 a night",
    "Vancouver next weekend, at least 4 stars",
    "Kelowna Dec 5 to 7, 2 adults",
]


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "unknown"


def too_many_fails(ip):
    count, start = _fails.get(ip, (0, 0.0))
    if time.time() - start > FAIL_WINDOW:
        _fails.pop(ip, None)
        return False
    return count >= MAX_FAILS


def record_fail(ip):
    count, start = _fails.get(ip, (0, time.time()))
    if time.time() - start > FAIL_WINDOW:
        count, start = 0, time.time()
    _fails[ip] = (count + 1, start)


@app.before_request
def require_password():
    if not APP_PASSWORD or request.endpoint == "login" or session.get("ok"):
        return None
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD or session.get("ok"):
        return redirect(url_for("home"))
    error = ""
    if request.method == "POST":
        ip = client_ip()
        if too_many_fails(ip):
            error = "Too many wrong tries. Wait 15 minutes, then try again."
        elif hmac.compare_digest(request.form.get("password", "").encode(), APP_PASSWORD.encode()):
            _fails.pop(ip, None)
            session.clear()
            session.permanent = True
            session["ok"] = True
            return redirect(url_for("home"))
        else:
            record_fail(ip)
            error = "That password isn't right. Check it and try again."
    return render_template_string(PAGE, login=True, error=error, show_signout=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login") if APP_PASSWORD else url_for("home"))


@app.route("/")
def home():
    q = request.args.get("q", "").strip()[:300]
    ctx = {"q": q, "examples": EXAMPLES, "searched": False, "error": "", "results": [],
           "summary": "", "understood": [], "cur": "CAD", "nights": 0,
           "login": False, "show_signout": bool(APP_PASSWORD)}
    if q:
        ctx["searched"] = True
        try:
            params, nights, results, summary = run_search(q)
            ctx.update(results=decorate(results), summary=summary, nights=nights,
                       understood=pills(params, nights), cur=params.get("currency", "CAD"))
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
    print(f"  Search limit: {DAILY_LIMIT} per day. Password: {'on' if APP_PASSWORD else 'off'}.")
    print("  Press Ctrl+C to stop.\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
