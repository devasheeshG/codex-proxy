"""Migrate legacy request archives to request.json.gz + response.json.gz.

The command is intentionally verify-first: without --delete-old it copies and
verifies every body while leaving the legacy manifest/SSE objects untouched.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config


def client(endpoint: str, access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "standard"}),
    )


def body_key(manifest_key: str, side: str) -> str:
    return manifest_key.rsplit("/manifest.json", 1)[0] + f"/{side}.json.gz"


def migrate_one(s3_read, s3_write, bucket: str, manifest_key: str) -> tuple[str, int, list[str]]:
    manifest = json.loads(s3_read.get_object(Bucket=bucket, Key=manifest_key)["Body"].read())
    legacy_keys: list[str] = []
    copied = 0
    for side in ("request", "response"):
        info = manifest.get(side)
        if not isinstance(info, dict) or not info.get("object_key"):
            continue
        old_key = str(info["object_key"])
        packed = s3_read.get_object(Bucket=bucket, Key=old_key)["Body"].read()
        raw = gzip.decompress(packed)
        expected = str(info.get("sha256") or "")
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError(f"hash mismatch: {bucket}/{old_key}")
        new_key = body_key(manifest_key, side)
        s3_write.put_object(
            Bucket=bucket,
            Key=new_key,
            Body=packed,
            ContentType=str(info.get("content_type") or "application/json"),
            ContentEncoding="gzip",
        )
        destination = s3_write.head_object(Bucket=bucket, Key=new_key)
        if int(destination.get("ContentLength", -1)) != len(packed):
            raise RuntimeError(f"verification size mismatch: {bucket}/{new_key}")
        copied += 1
        if old_key != new_key:
            legacy_keys.append(old_key)
    return manifest_key, copied, legacy_keys


def migrate_bucket(s3_read, s3_write, bucket: str, delete_old: bool, workers: int) -> tuple[int, int, int]:
    manifests = []
    for page in s3_read.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="raw/"):
        manifests.extend(str(item["Key"]) for item in page.get("Contents", []) if str(item.get("Key", "")).endswith("/manifest.json"))
    copied = deleted = 0
    results: dict[str, tuple[int, list[str]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(migrate_one, s3_read, s3_write, bucket, key): key for key in manifests}
        for index, future in enumerate(as_completed(futures), 1):
            completed_manifest, copied_count, legacy_keys = future.result()
            results[completed_manifest] = (copied_count, legacy_keys)
            copied += copied_count
            if index % 100 == 0 or index == len(manifests):
                print(f"{bucket}: {index}/{len(manifests)} manifests, copied={copied}, deleted={deleted}", flush=True)
    if delete_old:
        for manifest_key in manifests:
            _, legacy_keys = results[manifest_key]
            for old_key in legacy_keys + [manifest_key]:
                s3_write.delete_object(Bucket=bucket, Key=old_key)
                deleted += 1
    return len(manifests), copied, deleted


def cleanup_legacy_objects(s3_read, s3_write, bucket: str) -> int:
    """Delete only legacy names after a completed verification pass."""
    legacy_suffixes = ("/manifest.json", "/response.sse.gz", "/response.bin.gz")
    keys: list[str] = []
    for page in s3_read.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="raw/"):
        keys.extend(str(item["Key"]) for item in page.get("Contents", []) if str(item.get("Key", "")).endswith(legacy_suffixes))
    deleted = 0
    for offset in range(0, len(keys), 1000):
        batch = keys[offset : offset + 1000]
        result = s3_write.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
        errors = result.get("Errors", [])
        if errors:
            raise RuntimeError(f"legacy cleanup failed for {bucket}: {errors[:3]}")
        deleted += len(batch)
        print(f"{bucket}: deleted {deleted}/{len(keys)} legacy objects", flush=True)
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete-old", action="store_true", help="Delete verified manifests and legacy body names")
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Batch-delete legacy names after a separate successful verification pass",
    )
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    endpoint = os.environ["ARCHIVE_MIGRATION_ENDPOINT"]
    read = client(endpoint, os.environ["ARCHIVE_MIGRATION_READ_KEY"], os.environ["ARCHIVE_MIGRATION_READ_SECRET"])
    write = client(endpoint, os.environ["ARCHIVE_MIGRATION_WRITE_KEY"], os.environ["ARCHIVE_MIGRATION_WRITE_SECRET"])
    for bucket in os.environ["ARCHIVE_MIGRATION_BUCKETS"].split(","):
        if args.cleanup_only:
            print(f"Starting legacy cleanup for {bucket.strip()}", flush=True)
            print("deleted", cleanup_legacy_objects(read, write, bucket.strip()), flush=True)
            continue
        print(f"Starting {bucket} (delete_old={args.delete_old})", flush=True)
        print("result", migrate_bucket(read, write, bucket.strip(), args.delete_old, args.workers), flush=True)


if __name__ == "__main__":
    main()
