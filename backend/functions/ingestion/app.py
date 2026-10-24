"""Weekly ingestion: UN Comtrade API -> raw copy in S3 -> gaps computed -> DynamoDB -> raw copy deleted.

Every run reloads the whole history, from 2019 to the current year, and recomputes every gap.
Countries publish with their own delays, so a full reload is the simplest way to pick up late
declarations. Invoked by EventBridge Scheduler with no payload; for a test, specific years can be
passed: {"years": [2023, 2024]}.

Lambda retries a failed asynchronous invocation with the same request id, so the raw copies written
under raw/<request id>/ are read back instead of calling the API again.
"""
import datetime
import json
import os
from decimal import Decimal

import boto3

import comtrade
import gaps

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
table = boto3.resource("dynamodb").Table(os.environ["GAPS_TABLE"])
RAW_BUCKET = os.environ["RAW_BUCKET"]
KEY_PARAMETER = os.environ["COMTRADE_KEY_PARAMETER"]


FIRST_YEAR = 2019  # start of DNEXT's extract


def years_to_load(event):
    if event.get("years"):
        return sorted(int(y) for y in event["years"])
    return list(range(FIRST_YEAR, datetime.date.today().year + 1))


def raw_records(year, prefix, api_key):
    key = f"{prefix}{year}.json"
    try:
        body = s3.get_object(Bucket=RAW_BUCKET, Key=key)["Body"].read()
        print(f"{year}: raw copy found, Comtrade not called again")
        return json.loads(body)
    except s3.exceptions.NoSuchKey:
        pass
    records = comtrade.fetch_year(year, api_key)
    s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(records).encode())
    print(f"{year}: {len(records)} records fetched and copied to s3://{RAW_BUCKET}/{key}")
    return records


def dec(x):
    return Decimal(str(round(x, 6)))


def write(cells, weights):
    with table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
        for c in cells:
            item = {
                "pk": f"{c['exporter']}#{c['hs4']}#{c['freq']}",
                "sk": f"{c['period']}#{c['partner']}",
                "partner": c["partner"],
                "period": c["period"],
                "status": c["status"],
            }
            if c["status"] != "one_sided":
                for field in ("fob_usd_t", "cif_usd_t", "gap", "fob_value_usd", "cif_value_usd"):
                    if c[field] is not None:  # no gap when the FOB price is zero
                        item[field] = dec(c[field])
                item["months"] = c["months"]
            batch.put_item(Item=item)
        for (exporter, hs4, year), partners in weights.items():
            batch.put_item(Item={
                "pk": f"{exporter}#{hs4}#WEIGHTS",
                "sk": year,
                "partners": {p: dec(v) for p, v in partners.items()},
            })


def handler(event, context):
    event = event or {}
    api_key = ssm.get_parameter(Name=f"/{KEY_PARAMETER}", WithDecryption=True)["Parameter"]["Value"]
    prefix = f"raw/{context.aws_request_id}/"
    years = years_to_load(event)

    rows = []
    for year in years:
        rows += comtrade.normalize(raw_records(year, prefix, api_key))

    cells, weights = gaps.compute(rows, set(comtrade.EXPORTERS))
    write(cells, weights)

    # Raw data is only kept while the processing fails (DNEXT does not need it afterwards).
    for year in years:
        s3.delete_object(Bucket=RAW_BUCKET, Key=f"{prefix}{year}.json")

    anomalies = sum(1 for c in cells if c["status"].startswith("anomaly"))
    summary = {"years": years, "rows": len(rows), "cells": len(cells), "anomalies": anomalies}
    print(json.dumps(summary))
    return summary
