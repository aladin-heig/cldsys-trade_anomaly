"""External context: full English Wikipedia pages about the product, the two countries and the year.

Free API, no key. The pages stay inside the Lambda: only the model's answer and the list of sources
leave it, so their size never counts against the Step Functions payload limit (256 KB).
A failure here never fails the report: the model then works from the trade data alone.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from countries import country_name

API_URL = "https://en.wikipedia.org/w/api.php"
# Wikimedia asks API clients to identify themselves.
USER_AGENT = "trade-anomaly-platform/1.0 (HES-SO T-CloudSys student project)"
PAGES = 3
MAX_CHARS = 400_000  # about 100,000 tokens, to bound the cost and the duration of one report
TIMEOUT_S = 10

PRODUCTS = {
    "1001": "wheat", "1003": "barley", "1005": "maize", "1201": "soybeans",
    "1205": "rapeseed", "1206": "sunflower seeds", "2304": "soybean meal",
}


def queries(exporter, partner, hs4, period):
    """Search queries, most specific first: product, both countries and year, then without the partner.

    "<product> exports <exporter> <partner> <year>" ranked best on test cases: it finds, for instance,
    "Economic consequences of the Russo-Ukrainian war" for Russian wheat to Egypt in 2022 and
    "China–United States trade war" for American soybeans to China in 2019.
    """
    product, year = PRODUCTS.get(hs4, hs4), period[:4]
    return [
        f"{product} exports {country_name(exporter)} {country_name(partner)} {year}",
        f"{product} exports {country_name(exporter)} {year}",
    ]


def _get(params, attempts=3):
    url = f"{API_URL}?{urllib.parse.urlencode({**params, 'format': 'json'})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            # Wikipedia rate-limits anonymous clients (429): wait a little and try again.
            if err.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))


def search(query, limit=PAGES):
    hits = _get({"action": "query", "list": "search", "srsearch": query, "srlimit": limit})
    return [hit["title"] for hit in hits["query"]["search"]]


def page(title):
    """Plain text of a whole page and its URL. The API returns a full text for one page per request."""
    data = _get({
        "action": "query", "prop": "extracts|info", "explaintext": 1, "inprop": "url",
        "redirects": 1, "titles": title,
    })
    found = next(iter(data["query"]["pages"].values()))
    return {"title": found["title"], "url": found["fullurl"], "text": found.get("extract", "")}


def fetch(exporter, partner, hs4, period):
    """Up to three pages, best match first, cut to MAX_CHARS in total. Returns [] if Wikipedia fails."""
    try:
        titles = []
        for query in queries(exporter, partner, hs4, period):
            titles += [t for t in search(query) if t not in titles]
            if len(titles) >= PAGES:
                break
        pages, budget = [], MAX_CHARS
        for title in titles[:PAGES]:
            p = page(title)
            p["text"] = p["text"][:budget]
            budget -= len(p["text"])
            pages.append(p)
            if budget <= 0:
                break
        return pages
    except Exception as err:  # network, timeout, unexpected answer: the report goes on without it
        print(f"Wikipedia unavailable, report without external context: {err!r}")
        return []
