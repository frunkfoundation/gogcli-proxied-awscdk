"""Private, expiring S3 handoff for authorized full Drive downloads."""
import json
import os
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

import boto3
from botocore.config import Config

from errors import ProxyError, remaining_seconds
from onboarding_store import new_id
from proxy_http import _get_header, _sanitize_response_headers, read_response

MEDIA_TYPE = "application/vnd.gog.download+json"
# ponytail: stage at most 256 MiB in Lambda's /tmp; larger files need multipart streaming.
MAX_STAGED_BYTES = 256 * 1024 * 1024


def read_proxy_response(resp, *, method, url, headers, max_bytes, deadline):
    destination = urlsplit(url)
    eligible = (
        method == "GET" and destination.hostname == "www.googleapis.com"
        and re.fullmatch(r"/drive/v3/files/[^/]+(?:/export)?", destination.path)
        and (destination.path.endswith("/export") or parse_qs(destination.query).get("alt") == ["media"])
        and not _get_header(headers, "Range")
    )
    if eligible and _get_header(headers, "X-GOG-Response-Mode") == "signed-url" and resp.status_code == 200:
        return stage_response(resp, deadline=deadline)
    return read_response(resp, max_bytes=max_bytes, deadline=deadline)


def stage_response(resp, *, deadline):
    client, key, bucket = None, None, os.getenv("GOG_DOWNLOAD_BUCKET", "")
    try:
        if not bucket:
            raise ProxyError(503, "proxy downloads are not configured")
        headers = _sanitize_response_headers(resp.headers)
        headers["Cache-Control"] = "no-store"
        if len(json.dumps(headers).encode()) > 32 * 1024:
            raise ProxyError(502, "upstream download headers too large")
        with tempfile.TemporaryFile() as staged:
            total = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                remaining_seconds(deadline)
                total += len(chunk)
                if total > MAX_STAGED_BYTES:
                    raise ProxyError(502, "download exceeds proxy staging limit")
                staged.write(chunk)
            staged.seek(0)
            budget = remaining_seconds(deadline)
            client = boto3.client("s3", config=Config(
                signature_version="s3v4", s3={"addressing_style": "virtual"},
                connect_timeout=min(5, budget / 2), read_timeout=budget / 2,
                retries={"total_max_attempts": 1},
            ))
            key = "downloads/" + new_id("dl")
            client.put_object(Bucket=bucket, Key=key, Body=staged, ContentLength=total,
                              ServerSideEncryption="AES256", CacheControl="no-store",
                              ContentType=_get_header(headers, "Content-Type") or "application/octet-stream")
            remaining_seconds(deadline)
            url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=300)
            remaining_seconds(deadline)
        body = json.dumps({"url": url, "status": 200, "headers": headers, "size": total}, separators=(",", ":"))
        if len(body.encode()) > 64 * 1024:
            raise ProxyError(502, "download handoff too large")
        return {"statusCode": 200, "headers": {"Content-Type": MEDIA_TYPE, "Cache-Control": "no-store"},
                "body": body, "isBase64Encoded": False}
    except Exception as exc:
        if client is not None and key is not None:
            try:
                cleanup_budget = remaining_seconds(deadline, limit=1)
                cleanup = boto3.client("s3", config=Config(
                    connect_timeout=cleanup_budget / 2, read_timeout=cleanup_budget / 2,
                    retries={"total_max_attempts": 1},
                ))
                cleanup.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass  # Private objects also expire through the bucket lifecycle.
        if isinstance(exc, ProxyError):
            raise
        remaining_seconds(deadline)
        raise ProxyError(502, "proxy download staging failed") from exc
    finally:
        resp.close()
