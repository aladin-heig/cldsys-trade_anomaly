"""Mirror matching and anomaly flagging, as defined in the project brief.

unit price = value / quantity          (USD per tonne)
gap        = (CIF - FOB) / FOB
anomaly    = FOB > CIF  or  gap > 35 %

A zero FOB price with a non-zero CIF price makes the gap tend to infinity: flagged as above the
threshold, with no gap value. Both prices at zero: nothing to compare, not flagged.

The export declared by A towards B (FOB) is matched with the import declared by B from A (CIF),
for the same product and period. Export and import rows are never added together.
"""
from collections import defaultdict
import math

GAP_THRESHOLD = 0.35
FREQUENCIES = ("M", "Q", "Y")


def period_of(ym, freq):
    year, month = ym.split("-")
    if freq == "M":
        return ym
    if freq == "Q":
        return f"{year}-Q{(int(month) - 1) // 3 + 1}"
    return year


def classify(fob, cif):
    """Return (gap, status). The gap is None when the FOB price is zero (division by zero)."""
    if fob == 0:
        return None, "ok" if cif == 0 else "anomaly_gap_above_threshold"
    gap = (cif - fob) / fob
    if fob > cif:
        return gap, "anomaly_fob_above_cif"
    if gap > GAP_THRESHOLD:
        return gap, "anomaly_gap_above_threshold"
    return gap, "ok"


def compute(rows, exporters):
    """Return (cells, weights).

    cells: one dict per (exporter, partner, product, frequency, period).
    weights: {(exporter, product, year): {partner: declared trade value in USD}}.
    """
    exports, imports = {}, {}
    for r in rows:
        # A row with no quantity (exactly 0, or missing) is ignored, as if it had not been declared.
        # The other side's row stays: per DNEXT's notes it is a legitimate one-sided transaction.
        if math.isnan(r["qty_t"]) or r["qty_t"] <= 0:
            continue
        if r["flow"] == "Export" and r["reporter"] in exporters:
            key = (r["reporter"], r["partner"], r["hs4"], r["ym"])
            exports[key] = (r["value_usd"], r["qty_t"])
        elif r["flow"] == "Import" and r["partner"] in exporters:
            key = (r["partner"], r["reporter"], r["hs4"], r["ym"])
            imports[key] = (r["value_usd"], r["qty_t"])

    # Sum each side separately. Quarters and years only aggregate the months declared on both
    # sides, so that a month missing on one side does not create an artificial gap.
    sums = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])  # fob value, fob qty, cif value, cif qty, months
    # Every period with at least one declared row. Per DNEXT's notes, a flow declared on one side only
    # (e.g. Russian exports after 2021-12, seen only through partners' imports) is a legitimate
    # transaction: it is shown as "one_sided", never as an anomaly, at every frequency.
    declared = set()
    for key in exports.keys() | imports.keys():
        exporter, partner, hs4, ym = key
        periods = [(exporter, partner, hs4, freq, period_of(ym, freq)) for freq in FREQUENCIES]
        declared.update(periods)
        fob, cif = exports.get(key), imports.get(key)
        if not fob or not cif:
            continue
        for period_key in periods:
            s = sums[period_key]
            s[0] += fob[0]; s[1] += fob[1]; s[2] += cif[0]; s[3] += cif[1]; s[4] += 1

    cells = []
    for (exporter, partner, hs4, freq, period), (fv, fq, cv, cq, months) in sums.items():
        fob_price, cif_price = fv / fq, cv / cq
        gap, status = classify(fob_price, cif_price)
        cells.append({
            "exporter": exporter, "partner": partner, "hs4": hs4, "freq": freq, "period": period,
            "fob_usd_t": fob_price, "cif_usd_t": cif_price, "gap": gap, "status": status,
            "fob_value_usd": fv, "cif_value_usd": cv, "months": months,
        })
    for exporter, partner, hs4, freq, period in declared - sums.keys():
        cells.append({
            "exporter": exporter, "partner": partner, "hs4": hs4, "freq": freq, "period": period,
            "status": "one_sided",
        })

    # Trade weight of each partner per year, used to pick the top partners shown on the heatmap.
    # The larger of the two declared values counts, so that flows reported on one side only
    # (for example Russian exports after 2021) still weigh in.
    weights = defaultdict(lambda: defaultdict(float))
    for key in exports.keys() | imports.keys():
        exporter, partner, hs4, ym = key
        value = max(exports.get(key, (0, 0))[0], imports.get(key, (0, 0))[0])
        weights[(exporter, hs4, ym[:4])][partner] += value
    return cells, {key: dict(partners) for key, partners in weights.items()}
