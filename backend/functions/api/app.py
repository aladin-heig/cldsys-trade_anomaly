"""HTTP API behind API Gateway (JWT checked by the Cognito authorizer before this code runs).

GET  /anomalies?exporter=USA&hs4=1001&freq=M  heatmap: top 10 partners x periods
POST /reports  {exporter, partner, hs4, freq, period}  starts the report workflow
GET  /reports/{reportId}  status: not_generated | in_progress | failed | generated (+ download link)

The report status needs no table: the workflow execution is named after the anomaly, so
the PDF in S3 means "generated", otherwise the execution of that name says running or failed.
"""
import base64
import json
import os
import re
from collections import defaultdict
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table(os.environ["GAPS_TABLE"])
s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions")
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
EXECUTION_PREFIX = STATE_MACHINE_ARN.replace(":stateMachine:", ":execution:")
TOP_PARTNERS = 10
LINK_SECONDS = 900

EXPORTERS = {"ARG", "AUS", "BRA", "CAN", "FRA", "KAZ", "ROU", "RUS", "UKR", "USA"}
PRODUCTS = {"1001", "1003", "1005", "1201", "1205", "1206", "2304"}
PERIOD = {"M": r"\d{4}-\d{2}", "Q": r"\d{4}-Q[1-4]", "Y": r"\d{4}"}


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)),
    }


def query_all(pk):
    items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(pk)}
    while True:
        page = table.query(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def validate(exporter, partner, hs4, freq, period):
    """Return an error message, or None. The report id doubles as the execution name, so it must be strict."""
    if exporter not in EXPORTERS:
        return "unknown exporter"
    if not re.fullmatch(r"[A-Z0-9]{3}", partner or ""):
        return "partner must be an ISO3 code"
    if hs4 not in PRODUCTS:
        return "unknown HS4 code"
    if freq not in PERIOD or not re.fullmatch(PERIOD[freq], period or ""):
        return "frequency and period do not match"
    return None


def anomalies(params):
    exporter, hs4, freq = params.get("exporter"), params.get("hs4"), params.get("freq", "M")
    if exporter not in EXPORTERS or hs4 not in PRODUCTS or freq not in PERIOD:
        return respond(400, {"error": "exporter, hs4 and freq (M, Q or Y) are required"})

    total = defaultdict(float)
    for year in query_all(f"{exporter}#{hs4}#WEIGHTS"):
        for partner, value in year["partners"].items():
            total[partner] += float(value)
    partners = [p for p, _ in sorted(total.items(), key=lambda i: i[1], reverse=True)[:TOP_PARTNERS]]

    cells = [c for c in query_all(f"{exporter}#{hs4}#{freq}") if c["partner"] in partners]
    periods = sorted({c["period"] for c in cells})
    return respond(200, {
        "exporter": exporter, "hs4": hs4, "freq": freq,
        "partners": partners, "periods": periods,
        "cells": [{k: v for k, v in c.items() if k not in ("pk", "sk")} for c in cells],
    })


def start_report(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return respond(400, {"error": "invalid JSON"})
    fields = [str(data.get(k, "")) for k in ("exporter", "partner", "hs4", "freq", "period")]
    error = validate(*fields)
    if error:
        return respond(400, {"error": error})
    exporter, partner, hs4, freq, period = fields
    report_id = f"{exporter}-{partner}-{hs4}-{freq}-{period}"
    request = {"reportId": report_id, "exporter": exporter, "partner": partner, "hs4": hs4, "freq": freq, "period": period}
    try:
        sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, name=report_id, input=json.dumps(request))
    except sfn.exceptions.ExecutionAlreadyExists:
        # Same anomaly already requested: the existing execution (or its PDF) is the answer.
        pass
    return respond(202, {"reportId": report_id, **report_status(report_id)})


def report_status(report_id):
    key = f"reports/{report_id}.pdf"
    try:
        s3.head_object(Bucket=REPORTS_BUCKET, Key=key)
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": REPORTS_BUCKET, "Key": key}, ExpiresIn=LINK_SECONDS
        )
        return {"status": "generated", "downloadUrl": url}
    except s3.exceptions.ClientError as err:
        if err.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise
    try:
        execution = sfn.describe_execution(executionArn=f"{EXECUTION_PREFIX}:{report_id}")
    except sfn.exceptions.ExecutionDoesNotExist:
        return {"status": "not_generated"}
    if execution["status"] in ("RUNNING", "PENDING_REDRIVE", "SUCCEEDED"):
        # SUCCEEDED without a PDF only happens in the instant between the PDF step and this read.
        return {"status": "in_progress"}
    return {"status": "failed", "error": execution.get("error"), "cause": execution.get("cause")}


def get_report(report_id):
    parts = (report_id or "").split("-", 4)
    if len(parts) != 5 or validate(*parts):
        return respond(400, {"error": "invalid report id"})
    return respond(200, {"reportId": report_id, **report_status(report_id)})


def handler(event, context):
    route = event["routeKey"]
    if route == "GET /anomalies":
        return anomalies(event.get("queryStringParameters") or {})
    if route == "POST /reports":
        return start_report(event)
    if route == "GET /reports/{reportId}":
        return get_report(event["pathParameters"]["reportId"])
    return respond(404, {"error": "unknown route"})
