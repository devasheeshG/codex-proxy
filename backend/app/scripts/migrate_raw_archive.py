"""Convert legacy gzip archive objects to raw JSON without losing bytes.

The migration is streaming and bounded: one object is spooled to the configured
persistent work directory, uploaded and verified, then its temporary file is
removed. Source objects are deleted only when --delete-source is supplied.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import tempfile
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config


def client() -> object:
    return boto3.client(
        "s3",
        endpoint_url=os.environ["ARCHIVE_S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["ARCHIVE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["ARCHIVE_S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("ARCHIVE_S3_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "standard"}),
    )


def migrate_one(s3, bucket: str, key: str, work_dir: Path, delete_source: bool) -> tuple[int, int]:
    if not key.endswith(".json.gz"):
        return 0, 0
    destination = key[:-3]
    try:
        existing = s3.head_object(Bucket=bucket, Key=destination)
        if existing.get("ContentEncoding") != "gzip":
            if delete_source:
                s3.delete_object(Bucket=bucket, Key=key)
            return 0, int(existing.get("ContentLength", 0))
    except s3.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise
    fd, tmp_name = tempfile.mkstemp(prefix="archive-", suffix=".json.part", dir=work_dir)
    os.close(fd)
    raw_bytes = 0
    digest = hashlib.sha256()
    try:
        result = s3.get_object(Bucket=bucket, Key=key)
        with gzip.GzipFile(fileobj=result["Body"]) as source, open(tmp_name, "wb", buffering=1024 * 1024) as target:
            while chunk := source.read(8 * 1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
                raw_bytes += len(chunk)
        s3.upload_file(
            tmp_name,
            bucket,
            destination,
            ExtraArgs={"ContentType": "application/json", "Metadata": {"raw-sha256": digest.hexdigest()}},
            Config=TransferConfig(multipart_threshold=8 * 1024 * 1024, multipart_chunksize=8 * 1024 * 1024, max_concurrency=1, use_threads=False),
        )
        verified = s3.head_object(Bucket=bucket, Key=destination)
        if int(verified.get("ContentLength", -1)) != raw_bytes or verified.get("Metadata", {}).get("raw-sha256") != digest.hexdigest():
            raise RuntimeError(f"verification failed for {bucket}/{destination}")
        if delete_source:
            s3.delete_object(Bucket=bucket, Key=key)
        return 1, raw_bytes
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete-source", action="store_true", help="Delete a gzip source only after raw JSON verification")
    parser.add_argument("--work-dir", default=os.environ.get("ARCHIVE_MIGRATION_WORK_DIR", "/borg/work"))
    args = parser.parse_args()
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    s3 = client()
    bucket = os.environ["ARCHIVE_S3_BUCKET"]
    copied = deleted = raw_bytes = 0
    paginator = s3.get_paginator("list_objects_v2")
    keys = [
        str(o["Key"])
        for page in paginator.paginate(Bucket=bucket, Prefix="raw/")
        for o in page.get("Contents", [])
        if str(o.get("Key", "")).endswith(".json.gz")
    ]
    print(f"{bucket}: found {len(keys)} legacy gzip objects", flush=True)
    for index, key in enumerate(keys, 1):
        count, size = migrate_one(s3, bucket, key, work_dir, args.delete_source)
        copied += count
        raw_bytes += size
        if args.delete_source:
            deleted += count
        if index % 100 == 0 or index == len(keys):
            print(f"{bucket}: {index}/{len(keys)} copied={copied} deleted={deleted} raw_bytes={raw_bytes}", flush=True)


if __name__ == "__main__":
    main()
