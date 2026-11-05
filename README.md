# hotel-agent

I got tired of checking Google Hotels every few days before a trip, so I built a small tool that does it for me.

You type what you want in plain English, like "Victoria, Nov 20 to 23, under $150 a night, at least 3.5 stars", and it gives back the cheapest places that fit, sorted by total price for the whole stay. There's also a watcher that saves a search and tells you if the price drops.

## How it works

Claude reads your request and turns it into the actual search settings: city, dates, budget and minimum rating. Then regular Python takes over. It pulls prices from Google Hotels through SerpApi, filters out anything over budget or under the rating, and sorts what's left. At the end Claude writes a couple of sentences about the results.

I kept the AI part small on purpose. It's good at understanding messy requests, but I didn't want it anywhere near the prices or the sorting. That part is just code, so the numbers you see are exactly what SerpApi returned.

## Setup

You'll need Python 3.10+, a free SerpApi account, and an Anthropic API key with a few dollars of credit.

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env

Then put your two keys in `.env`. Don't commit that file. It's already in `.gitignore`.

## Using it

One thing that tripped me up: wrap your request in single quotes. With double quotes, the shell treats `$150` as a variable and quietly deletes it, so your budget never gets used.

    python hotel_agent.py 'Victoria BC, Nov 20 to 23, under $150 a night'
    python hotel_agent.py 'Kelowna Dec 5 to 7, at least 3.5 stars' --top 10 --no-summary

For the price watcher:

    python price_watch.py add 'Victoria BC, Nov 20 to 23, under $150 a night, at least 3.5 stars'
    python price_watch.py list
    python price_watch.py check
    python price_watch.py remove 1

`check` compares the current cheapest price with the last one it saw. If it dropped, you get a message, a line in `alerts.log`, and a Mac notification. You can run it from cron if you want it to check on its own, but it won't run while your laptop is asleep.

## Things to know

- Some Google Hotels prices don't include taxes and fees. Check the real total on the booking page before you get excited.
- SerpApi's free plan gives you 250 searches a month, and every search or check uses one. Two checks a day on one trip is about 60 a month.
- The Claude side costs basically nothing, a tiny fraction of a cent per search.

## What I'd add next

A small web page so I don't have to use the terminal, email alerts, and a chart showing how the price changed over time.
