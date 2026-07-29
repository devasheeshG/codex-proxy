"""Bounded MinIO-to-Borg retention worker.

Only expired raw JSON objects are archived. Borg archives are never pruned or
deleted by this worker. MinIO deletion is opt-in and occurs only after Borg
successfully commits and lists the batch archive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["ARCHIVE_S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["ARCHIVE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["ARCHIVE_S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("ARCHIVE_S3_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "standard"}),
    )


def run_once() -> None:
    s3 = s3_client()
    bucket = os.environ["ARCHIVE_S3_BUCKET"]
    prefix = os.environ.get("ARCHIVE_S3_PREFIX", "").strip("/")
    work = Path(os.environ.get("ARCHIVE_RETENTION_WORK_DIR", "/borg/work")).resolve()
    repo = Path(os.environ.get("ARCHIVE_BORG_REPOSITORY", "/borg/repository")).resolve()
    batch_limit = int(os.environ.get("ARCHIVE_RETENTION_BATCH_BYTES", str(512 * 1024 * 1024)))
    min_free = int(os.environ.get("ARCHIVE_RETENTION_MIN_FREE_GIB", "10")) * 1024**3
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(os.environ.get("ARCHIVE_HOT_RETENTION_DAYS", "4")))
    work.mkdir(parents=True, exist_ok=True)
    repo.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(work).free < min_free:
        print("retention paused: persistent disk is below ARCHIVE_RETENTION_MIN_FREE_GIB", flush=True)
        return
    if not (repo / "config").exists():
        subprocess.run(["borg", "init", "--encryption=none", str(repo)], check=True)
    batch_root = work / "batch"
    if batch_root.exists():
        shutil.rmtree(batch_root)
    batch_root.mkdir()
    batch: list[tuple[str, Path]] = []
    batch_bytes = 0
    batch_number = 0

    def flush() -> None:
        nonlocal batch_bytes, batch_number
        if not batch:
            return
        archive_name = f"raw-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{batch_number:06d}"
        subprocess.run(["borg", "create", "--compression", "zstd,3", "--stats", f"{repo}::{archive_name}", str(batch_root)], check=True)
        subprocess.run(["borg", "list", f"{repo}::{archive_name}"], check=True, stdout=subprocess.DEVNULL)
        if os.environ.get("ARCHIVE_RETENTION_DELETE_MINIO", "false").lower() == "true":
            for key, _ in batch:
                s3.delete_object(Bucket=bucket, Key=key)
        print(
            f"retention archived={len(batch)} bytes={batch_bytes} "
            f"archive={archive_name} "
            f"minio_deleted={os.environ.get('ARCHIVE_RETENTION_DELETE_MINIO', 'false')}",
            flush=True,
        )
        for child in batch_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        batch.clear()
        batch_bytes = 0
        batch_number += 1

    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for item in page.get("Contents", []):
            key = str(item.get("Key", ""))
            if not key.endswith(".json"):
                continue
            parts = key.split("/")
            try:
                year = int(next(p.split("=", 1)[1] for p in parts if p.startswith("year=")))
                month = int(next(p.split("=", 1)[1] for p in parts if p.startswith("month=")))
                day = int(next(p.split("=", 1)[1] for p in parts if p.startswith("day=")))
                hour = int(next(p.split("=", 1)[1] for p in parts if p.startswith("hour=")))
                event_time = datetime(year, month, day, hour, tzinfo=timezone.utc)
            except (StopIteration, ValueError):
                continue
            if event_time >= cutoff:
                continue
            if shutil.disk_usage(work).free < min_free + batch_limit:
                print("retention paused: batch would violate persistent disk floor", flush=True)
                break
            relative = Path(*parts[1:]) if parts and parts[0] == "raw" else Path(*parts)
            destination = batch_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            body = s3.get_object(Bucket=bucket, Key=key)["Body"]
            with destination.open("wb", buffering=1024 * 1024) as out:
                while chunk := body.read(8 * 1024 * 1024):
                    out.write(chunk)
            body.close()
            size = destination.stat().st_size
            batch.append((key, destination))
            batch_bytes += size
            if batch_bytes >= batch_limit:
                flush()
        else:
            continue
        break
    flush()


def main() -> None:
    if os.environ.get("ARCHIVE_RETENTION_ENABLED", "false").lower() != "true":
        print("retention disabled (ARCHIVE_RETENTION_ENABLED is not true)", flush=True)
        while True:
            time.sleep(max(60, int(os.environ.get("ARCHIVE_RETENTION_INTERVAL_SECONDS", "3600"))))
    interval = max(60, int(os.environ.get("ARCHIVE_RETENTION_INTERVAL_SECONDS", "3600")))
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            print(f"retention cycle failed: {exc!r}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
