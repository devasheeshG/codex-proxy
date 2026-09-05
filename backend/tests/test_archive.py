import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.utils.archive import RawArchiveMiddleware, S3ArchiveStore


class FakeS3:
    def __init__(self, *, fail_put: bool = False) -> None:
        self.objects: Dict[str, bytes] = {}
        self.fail_put = fail_put
        self.uploads: Dict[str, Dict[str, Any]] = {}
        self.next_upload = 0

    def head_bucket(self, **kwargs):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def put_object(self, *, Bucket, Key, Body, **kwargs):  # noqa: N803
        if self.fail_put:
            raise RuntimeError("simulated S3 outage")
        self.objects[Key] = bytes(Body)
        return {"ETag": '"fake"'}

    def create_multipart_upload(self, *, Bucket, Key, **kwargs):  # noqa: N803
        self.next_upload += 1
        upload_id = f"upload-{self.next_upload}"
        self.uploads[upload_id] = {"bucket": Bucket, "key": Key, "parts": {}}
        return {"UploadId": upload_id}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body, **kwargs):  # noqa: N803
        self.uploads[UploadId]["parts"][PartNumber] = bytes(Body)
        return {"ETag": f'"part-{PartNumber}"'}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload, **kwargs):  # noqa: N803
        upload = self.uploads.pop(UploadId)
        self.objects[Key] = b"".join(upload["parts"][part["PartNumber"]] for part in MultipartUpload["Parts"])
        return {"ETag": '"complete"'}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId, **kwargs):  # noqa: N803
        self.uploads.pop(UploadId, None)


def _settings(*, required: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        ARCHIVE_ENABLED=True,
        ARCHIVE_REQUIRED=required,
        ARCHIVE_S3_BUCKET="codex-proxy",
        ARCHIVE_S3_PREFIX="raw",
        ARCHIVE_MULTIPART_PART_SIZE_MB=5,
        ARCHIVE_S3_REGION="us-east-1",
        ARCHIVE_S3_ENDPOINT_URL="http://minio:9000",
        ARCHIVE_S3_ACCESS_KEY_ID="access",
        ARCHIVE_S3_SECRET_ACCESS_KEY="secret",
        ARCHIVE_S3_FORCE_PATH_STYLE=True,
        ARCHIVE_S3_MAX_ATTEMPTS=1,
    )


def _scope(path: str = "/api/v1/responses") -> Dict[str, Any]:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"authorization", b"Bearer secret")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "state": {},
    }


def _run(app, scope, messages):
    received = iter(messages)
    sent: List[Dict[str, Any]] = []

    async def receive():
        return next(received)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _archive_objects(fake: FakeS3) -> Dict[str, bytes]:
    return {key: value for key, value in fake.objects.items() if "/event_id=" in key}


def test_archive_preserves_json_request_and_sse_response():
    fake = FakeS3()
    store = S3ArchiveStore(_settings(), client=fake)

    async def app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        scope["state"]["archive_metadata"]["model"] = "gpt-test"
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b'data: {"delta":"hello"}\n\n', "more_body": True})
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})

    sent = _run(
        RawArchiveMiddleware(app, settings=_settings(), store=store),
        _scope(),
        [{"type": "http.request", "body": b'{"input":"hello"}', "more_body": False}],
    )
    assert sent[0]["type"] == "http.response.start"
    assert any(key.endswith("/request.json") for key in fake.objects)
    assert any(key.endswith("/response.json") for key in fake.objects)
    assert not any(key.endswith("/manifest.json") for key in fake.objects)
    request_key = next(key for key in fake.objects if key.endswith("/request.json"))
    response_key = next(key for key in fake.objects if key.endswith("/response.json"))
    assert fake.objects[request_key] == b'{"input":"hello"}'
    assert fake.objects[response_key] == b'data: {"delta":"hello"}\n\ndata: [DONE]\n\n'


def test_archive_records_interrupted_stream():
    fake = FakeS3()
    store = S3ArchiveStore(_settings(), client=fake)

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: partial\n\n", "more_body": True})
        raise RuntimeError("upstream disconnected")

    with pytest.raises(RuntimeError, match="upstream disconnected"):
        _run(RawArchiveMiddleware(app, settings=_settings(), store=store), _scope(), [{"type": "http.request", "body": b"{}", "more_body": False}])
    assert any(key.endswith("/request.json") for key in fake.objects)
    assert any(key.endswith("/response.json") for key in fake.objects)
    assert not any(key.endswith("/manifest.json") for key in fake.objects)


def test_required_archive_failure_returns_503():
    fake = FakeS3(fail_put=True)
    store = S3ArchiveStore(_settings(), client=fake)

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"should not be sent", "more_body": False})

    sent = _run(RawArchiveMiddleware(app, settings=_settings(), store=store), _scope(), [{"type": "http.request", "body": b"{}", "more_body": False}])
    assert sent[0]["status"] == 503
    assert json.loads(sent[1]["body"])["error"]["type"] == "archive_unavailable"


def test_large_body_is_written_as_one_complete_object():
    fake = FakeS3()
    store = S3ArchiveStore(_settings(), client=fake)

    async def write_body():
        source = bytes(range(256)) * 4
        return source, await store.put_body("raw/complete.json", source, "application/json")

    source, archived = asyncio.run(write_body())
    assert archived.raw_bytes == len(source)
    assert fake.objects[archived.key] == source
    assert fake.uploads == {}


def test_non_inference_paths_are_not_archived():
    fake = FakeS3()
    store = S3ArchiveStore(_settings(), client=fake)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    sent = _run(RawArchiveMiddleware(app, settings=_settings(), store=store), _scope("/api/v1/models"), [])
    assert sent[-1]["body"] == b"ok"
    assert fake.objects == {}
