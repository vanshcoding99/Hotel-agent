"""Price watcher: save a hotel search, re-check it, alert when the cheapest total drops.

Usage:
    python price_watch.py add "Victoria BC, Nov 20 to 23, under $150 a night"
    python price_watch.py list
    python price_watch.py check
    python price_watch.py remove 1

Each check uses 1 SerpApi search per saved watch.
"""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from hotel_agent import fetch_hotels, nights_between, normalize, parse_request

BASE = Path(__file__).resolve().parent
WATCH_FILE = BASE / "watches.json"
LOG_FILE = BASE / "alerts.log"


def load_watches():
    if WATCH_FILE.exists():
        return json.loads(WATCH_FILE.read_text())
    return []


def save_watches(watches):
    WATCH_FILE.write_text(json.dumps(watches, indent=2))


def cheapest(params):
    """Return the cheapest hotel for these search params, or None."""
    nights = nights_between(params["check_in"], params["check_out"])
    props = fetch_hotels(params)
    results = normalize(props, nights, params.get("max_price_per_night"), params.get("min_rating"))
    return results[0] if results else None


def alert(message):
    """Print, log to a file, and try a macOS notification."""
    print(message)
    with LOG_FILE.open("a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    safe = message.replace('"', "'")
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe}" with title "Hotel price drop"'],
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # not on a Mac, or notification failed; the log file still has it


def cmd_add(text):
    params = parse_request(text)
    best = cheapest(params)
    if best is None:
        sys.exit("No hotels found for that search, so nothing was saved.")
    now = datetime.now().isoformat(timespec="seconds")
    watches = load_watches()
    watches.append(
        {
            "request": text,
            "params": params,
            "last_total": best["total"],
            "last_name": best["name"],
            "history": [{"time": now, "total": best["total"], "name": best["name"]}],
        }
    )
    save_watches(watches)
    cur = params.get("currency", "CAD")
    print(f"Watching #{len(watches)}: cheapest now is {best['name']} at {cur} {best['total']:.0f} total.")


def cmd_list():
    watches = load_watches()
    if not watches:
        print("No saved watches.")
        return
    for i, w in enumerate(watches, 1):
        cur = w["params"].get("currency", "CAD")
        print(f"{i}. {w['request']}")
        print(f"   last: {w['last_name']} at {cur} {w['last_total']:.0f} ({len(w['history'])} checks)")


def cmd_check():
    watches = load_watches()
    if not watches:
        print("No saved watches. Add one first.")
        return
    today = date.today().isoformat()
    for i, w in enumerate(watches, 1):
        p = w["params"]
        if p["check_in"] < today:
            print(f"#{i} skipped, the stay has already started: {w['request']}")
            continue
        try:
            best = cheapest(p)
        except (Exception, SystemExit) as e:  # one failed watch should not stop the rest
            print(f"#{i} check failed: {e}")
            continue
        if best is None:
            print(f"#{i} no results this time.")
            continue
        cur = p.get("currency", "CAD")
        old = w["last_total"]
        w["history"].append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "total": best["total"],
                "name": best["name"],
            }
        )
        if best["total"] < old:
            alert(
                f"#{i} {p['location']} {p['check_in']} to {p['check_out']}: "
                f"{best['name']} now {cur} {best['total']:.0f} (was {cur} {old:.0f})"
            )
        else:
            print(f"#{i} no drop. Cheapest is {best['name']} at {cur} {best['total']:.0f} (last {cur} {old:.0f}).")
        w["last_total"] = best["total"]
        w["last_name"] = best["name"]
    save_watches(watches)


def cmd_remove(index):
    watches = load_watches()
    if not 1 <= index <= len(watches):
        sys.exit(f"No watch #{index}. Use 'list' to see numbers.")
    removed = watches.pop(index - 1)
    save_watches(watches)
    print(f"Removed: {removed['request']}")


def main():
    parser = argparse.ArgumentParser(description="Hotel price watcher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="save a new watch")
    p_add.add_argument("request", nargs="+")
    sub.add_parser("list", help="show saved watches")
    sub.add_parser("check", help="re-check all watches and alert on drops")
    p_rm = sub.add_parser("remove", help="delete a watch by number")
    p_rm.add_argument("index", type=int)
    args = parser.parse_args()

    if args.cmd == "add":
        cmd_add(" ".join(args.request))
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "check":
        cmd_check()
    elif args.cmd == "remove":
        cmd_remove(args.index)


if __name__ == "__main__":
    main()
