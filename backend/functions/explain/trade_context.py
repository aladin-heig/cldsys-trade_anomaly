"""Internal context of a flagged cell, taken from the gaps table: the pair's history and the other partners."""
from decimal import Decimal

HISTORY_LENGTH = 36


def plain(value):
    """DynamoDB returns Decimal; the model and the next step expect plain JSON numbers."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if k not in ("pk", "sk")}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def select(cells, partner, period):
    """Split the cells of one exporter, product and frequency into (anomaly, history, peers).

    history: the pair's last periods up to the flagged one, oldest first.
    peers: the other partners in the same period, for comparison. One-sided peers stay: if the
    exporter declared nothing towards anyone, the cause lies with the exporter, not with the pair.
    """
    cells = [plain(c) for c in cells]
    pair = sorted((c for c in cells if c["partner"] == partner), key=lambda c: c["period"])
    anomaly = next((c for c in pair if c["period"] == period), None)
    history = [c for c in pair if c["period"] <= period][-HISTORY_LENGTH:]
    peers = [c for c in cells if c["period"] == period and c["partner"] != partner]
    return anomaly, history, peers
