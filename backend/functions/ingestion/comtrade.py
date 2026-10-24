"""Client for the UN Comtrade data API (monthly HS trade, totals only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/M/HS"

# Comtrade numeric codes of the ten exporters in DNEXT's scope.
# Comtrade uses 842 for the USA and 251 for France (not the M49 codes 840 / 250).
EXPORTERS = {
    "ARG": 32, "AUS": 36, "BRA": 76, "CAN": 124, "FRA": 251,
    "KAZ": 398, "ROU": 642, "RUS": 643, "UKR": 804, "USA": 842,
}
PRODUCTS = ["1001", "1003", "1005", "1201", "1205", "1206", "2304"]
WORLD_CODE = 0

# Totals only: no second partner, all customs procedures, all modes of transport.
TOTALS = {"partner2Code": "0", "customsCode": "C00", "motCode": "0"}


def months_of_year(year):
    return [f"{year}{m:02d}" for m in range(1, 13)]


def _get(params, api_key, attempts=5):
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.load(response)
            if body.get("error"):
                raise RuntimeError(f"Comtrade error: {body['error']}")
            return body.get("data") or []
        except urllib.error.HTTPError as err:
            # 429 = rate limit (1 call per second on the free tier), 5xx = transient.
            if err.code != 429 and err.code < 500:
                raise
            time.sleep(2 ** attempt + 1)
    raise RuntimeError(f"Comtrade still failing after {attempts} attempts: {url}")


def fetch_year(year, api_key):
    """Both sides of every flow for one calendar year: exporters' exports and partners' imports."""
    common = {
        "period": ",".join(months_of_year(year)),
        "cmdCode": ",".join(PRODUCTS),
        "includeDesc": "true",
        **TOTALS,
    }
    exporter_codes = ",".join(str(code) for code in EXPORTERS.values())
    exports = _get({**common, "reporterCode": exporter_codes, "flowCode": "X"}, api_key)
    time.sleep(1.1)
    imports = _get({**common, "partnerCode": exporter_codes, "flowCode": "M"}, api_key)
    time.sleep(1.1)
    return exports + imports


def normalize(records):
    """Map raw API records to the columns of DNEXT's file (YearMonth, Reporter, Flow, Partner, HS4, Value_USD, Qty_t)."""
    rows = []
    for r in records:
        if r.get("partnerCode") == WORLD_CODE or not r.get("partnerISO") or not r.get("reporterISO"):
            continue
        period = str(r["period"])
        rows.append({
            "ym": f"{period[:4]}-{period[4:6]}",
            "reporter": r["reporterISO"],
            "flow": "Export" if r["flowCode"] == "X" else "Import",
            "partner": r["partnerISO"],
            "hs4": str(r["cmdCode"]),
            # primaryValue is FOB for exports and CIF for imports.
            "value_usd": float(r.get("primaryValue") or 0),
            "qty_t": float(r.get("netWgt") or 0) / 1000,
        })
    return rows
