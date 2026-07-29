"""Move archive objects from the legacy ``raw/`` prefix to bucket root.

Uses MinIO's server-side copy, so request bodies never pass through local disk.
Each object is verified before its legacy key is deleted.
"""

from __future__ import annotations

import argparse
import os

import boto3
from botocore.config import Config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-objects", type=int, default=0)
    args = parser.parse_args()
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["ARCHIVE_S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["ARCHIVE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["ARCHIVE_S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("ARCHIVE_S3_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket = os.environ["ARCHIVE_S3_BUCKET"]
    keys = [
        (str(obj["Key"]), int(obj.get("Size", 0)))
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="raw/")
        for obj in page.get("Contents", [])
        if str(obj.get("Key", "")).endswith(".json")
    ]
    if args.max_objects > 0:
        keys = keys[: args.max_objects]
    moved = 0
    for index, (source, source_size) in enumerate(keys, 1):
        destination = source.removeprefix("raw/")
        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source}, Key=destination, ContentType="application/json")
        destination_head = s3.head_object(Bucket=bucket, Key=destination)
        if source_size != int(destination_head["ContentLength"]):
            raise RuntimeError(f"verification failed for {source}")
        s3.delete_object(Bucket=bucket, Key=source)
        moved += 1
        if index % 100 == 0 or index == len(keys):
            print(f"{bucket}: {index}/{len(keys)} moved={moved}", flush=True)


if __name__ == "__main__":
    main()
