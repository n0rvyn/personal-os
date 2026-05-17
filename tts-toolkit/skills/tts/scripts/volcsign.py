#!/usr/bin/env python3
"""
volcsign.py — Signs a Volcengine OpenAPI request with HMAC-SHA256 V4 signature
and prints the response JSON to stdout.

Usage:
    python3 volcsign.py <Action> <Service> <Region> <body_json>

Example:
    python3 volcsign.py UsageMonitoring speech_saas_prod cn-beijing '{"ProjectName":"default","ResourceID":"volc.service_type.10029",...}'

Credentials are read from environment, preferring the IAM-prefixed names. Fallback
to legacy unprefixed names is kept for back-compat with older env files.
  - Preferred: VOLC_IAM_ACCESS_KEY_ID, VOLC_IAM_SECRET_ACCESS_KEY
  - Legacy:    VOLC_ACCESS_KEY_ID,     VOLC_SECRET_ACCESS_KEY

API version defaults to 2025-05-21 (covers UsageMonitoring + QuotaMonitoring for
speech_saas_prod). Override per-call via VOLC_API_VERSION env if calling an
action that lives at a different version.

Security constraints:
  - Credentials are read from environment ONLY.
  - sys.tracebacklimit = 0 prevents stack traces from leaking local variables.
  - HTTPError handler prints ONLY {Code, Message} fields; never raw headers or body.
  - No --debug flag, no env dump, no request header dump.

Exit codes: 0 success / 2 HTTP or network error / 3 auth missing
"""
import sys
import os
import json
import hashlib
import hmac
import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.tracebacklimit = 0

def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

VOLC_API_VERSION = os.environ.get("VOLC_API_VERSION", "2025-05-21")

def _sign(action: str, service: str, region: str, body: str) -> dict:
    """Build Volcengine V4 signed headers. Returns dict of headers to send."""
    ak = os.environ.get("VOLC_IAM_ACCESS_KEY_ID") or os.environ.get("VOLC_ACCESS_KEY_ID", "")
    sk = os.environ.get("VOLC_IAM_SECRET_ACCESS_KEY") or os.environ.get("VOLC_SECRET_ACCESS_KEY", "")
    if not ak or not sk:
        sys.stderr.write(
            "volcsign: VOLC_IAM_ACCESS_KEY_ID + VOLC_IAM_SECRET_ACCESS_KEY required "
            "(legacy VOLC_ACCESS_KEY_ID/VOLC_SECRET_ACCESS_KEY also accepted)\n"
        )
        sys.exit(3)

    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y%m%d")
    datetime_str = now.strftime("%Y%m%dT%H%M%SZ")

    # VOLC_API_HOST override exists for tests that need to force a network error
    # against an unreachable host without hitting the real Volc API. Production
    # callers leave it unset and get the default.
    host = os.environ.get("VOLC_API_HOST", "open.volcengineapi.com")
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    # Canonical request
    canonical_headers = (
        f"content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{body_hash}\n"
        f"x-date:{datetime_str}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    query_string = f"Action={action}&Version={VOLC_API_VERSION}"
    canonical_request = "\n".join([
        "POST",
        "/",
        query_string,
        canonical_headers,
        signed_headers,
        body_hash,
    ])

    # String to sign
    credential_scope = f"{date_str}/{region}/{service}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256",
        datetime_str,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    # Signing key
    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(
                _hmac_sha256(sk.encode("utf-8"), date_str),
                region,
            ),
            service,
        ),
        "request",
    )

    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Host": host,
        "X-Content-Sha256": body_hash,
        "X-Date": datetime_str,
    }


def main() -> None:
    if len(sys.argv) != 5:
        sys.stderr.write(
            "usage: volcsign.py <Action> <Service> <Region> <body_json>\n"
        )
        sys.exit(2)

    action, service, region, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

    # Validate body is JSON before signing
    try:
        json.loads(body)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"volcsign: body_json is not valid JSON: {e}\n")
        sys.exit(2)

    headers = _sign(action, service, region, body)
    host = os.environ.get("VOLC_API_HOST", "open.volcengineapi.com")
    url = f"https://{host}/?Action={action}&Version={VOLC_API_VERSION}"

    req = Request(url, data=body.encode("utf-8"), headers=headers, method="POST")

    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            print(raw)
    except HTTPError as e:
        raw_body = e.read()
        try:
            err_json = json.loads(raw_body)
            code = err_json.get("ResponseMetadata", {}).get("Error", {}).get("Code", "unknown")
            msg = err_json.get("ResponseMetadata", {}).get("Error", {}).get("Message", "")
            sys.stderr.write(f"volcsign: HTTP {e.code} Code={code} Message={msg}\n")
        except (json.JSONDecodeError, AttributeError):
            sys.stderr.write(f"volcsign: HTTP {e.code} (response not JSON, body suppressed)\n")
        sys.exit(2)
    except URLError as e:
        sys.stderr.write(f"volcsign: network error: {e.reason}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
