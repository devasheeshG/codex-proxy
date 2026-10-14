import asyncio
import json

import httpx

from app.routes import proxy


def frame(kind, **fields):
    return b"data: " + json.dumps({"type": kind, **fields}).encode() + b"\n\n"


def test_late_failure_is_preserved_with_usage():
    raw = frame("response.output_text.delta", delta="hello") + frame(
        "response.failed", response={"id": "resp_real", "status": "failed", "usage": {"input_tokens": 12, "output_tokens": 3}}
    )
    assert proxy._sanitize_sse_payload(raw) == raw


def test_buffered_history_capacity_text_is_not_a_provider_failure():
    async def run():
        raw = frame("response.created", response={"id": "resp_real", "instructions": "at capacity; response.failed"})
        raw += frame("response.completed", response={"id": "resp_real", "status": "completed"})
        candidate = await proxy._prepare_candidate(httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw))
        assert not candidate._proxy_capacity_error

    asyncio.run(run())


def test_pending_read_transport_error_is_propagated():
    async def run():
        async def read():
            raise httpx.ReadError("broken transport")

        async def empty():
            if False:
                yield b""

        candidate = proxy._PrefetchedResponse(httpx.Response(200), b"", empty(), False, pending_read=asyncio.create_task(read()))
        try:
            await candidate.aread()
        except httpx.ReadError:
            return
        raise AssertionError("Transport failure was hidden")

    asyncio.run(run())


def test_headerless_slow_stream_returns_prefix_and_resumes(monkeypatch):
    async def run():
        release = asyncio.Event()
        closed = False

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield frame("response.created", response={"id": "resp_real"})
                await release.wait()
                yield frame("response.completed", response={"id": "resp_real", "status": "completed"})

            async def aclose(self):
                nonlocal closed
                closed = True

        monkeypatch.setattr(proxy, "_SSE_PREFETCH_SECONDS", 0.02)
        response = httpx.Response(200, stream=Stream())
        candidate = await asyncio.wait_for(proxy._prepare_candidate(response), 0.2)
        assert candidate.headers["content-type"] == "text/event-stream"
        it = candidate.aiter_bytes()
        assert b"response.created" in await anext(it)
        release.set()
        assert b"response.completed" in await anext(it)
        await candidate.aclose()
        assert closed

    asyncio.run(run())


def test_silent_stream_gets_keepalive_without_canceling_upstream(monkeypatch):
    async def run():
        release = asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                await release.wait()
                yield frame("response.completed", response={"id": "resp_real"})

        monkeypatch.setattr(proxy, "_SSE_KEEPALIVE_SECONDS", 0.01)
        it = proxy._iter_sanitized_sse(httpx.Response(200, stream=Stream()))
        assert await anext(it) == b": proxy keepalive\n\n"
        release.set()
        assert b"resp_real" in await anext(it)
        await it.aclose()

    asyncio.run(run())


def test_error_words_in_history_do_not_hide_real_failure():
    raw = frame("response.created", response={"instructions": "response.output_text.delta at capacity"})
    raw += frame("response.failed", response={"status": "failed", "error": {"message": "server broke"}})
    assert proxy._classify_sse_prefix(raw) == (False, True, False)


def test_close_cancels_pending_read_and_closes_transport(monkeypatch):
    async def run():
        canceled = asyncio.Event()
        closed = False

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    await asyncio.Event().wait()
                    yield b""
                finally:
                    canceled.set()

            async def aclose(self):
                nonlocal closed
                closed = True

        monkeypatch.setattr(proxy, "_SSE_PREFETCH_SECONDS", 0.01)
        candidate = await proxy._prepare_candidate(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream()))
        await candidate.aclose()
        assert canceled.is_set() and closed

    asyncio.run(run())


def test_late_failure_keeps_reported_tokens_cost_and_tps(client, admin_headers, seed_account, make_user):
    import respx

    from app.utils.postgres import UsageRecordDb
    from app.utils.postgres.base import SessionFactory

    seed_account("late-failure")
    secret = make_user("late-failure-user")
    raw = frame("response.output_text.delta", delta="partial output")
    raw += frame(
        "response.failed",
        response={
            "id": "resp_real",
            "status": "failed",
            "model": "gpt-6.1-sol",
            "error": {"message": "provider failed after output"},
            "usage": {"input_tokens": 120, "output_tokens": 30},
        },
    )
    with respx.mock:
        respx.route(host="testserver").pass_through()
        route = respx.post("https://chatgpt.com/backend-api/codex/responses").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)
        )
        response = client.post(
            "/api/v1/responses",
            headers={"Authorization": "Bearer " + secret},
            json={
                "model": "gpt-6.1-sol",
                "input": "test",
                "stream": True,
            },
        )
    assert response.status_code == 200
    assert "response.failed" in response.text
    assert "resp_synth_" not in response.text
    assert "response.completed" not in response.text
    assert route.call_count == 1
    with SessionFactory() as db:
        row = db.query(UsageRecordDb).one()
        assert (row.input_tokens, row.output_tokens) == (120, 30)
        assert row.billed_cost_usd > 0 and row.tokens_per_second > 0
        rid = row.request_id
    events = client.get("/api/v1/events", params={"request_id": rid}, headers=admin_headers).json()["events"]
    assert events
    assert all(e["metadata"]["request_outcome"] == "failed" for e in events)
    assert all(e["metadata"]["input_tokens"] == 120 and e["metadata"]["output_tokens"] == 30 for e in events)


def test_delayed_pre_output_failure_retries_next_account(monkeypatch, client, seed_account, make_user):
    import respx

    class DelayedFailure(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame("response.created", response={"id": "resp_first", "status": "in_progress"})
            await asyncio.sleep(0.05)
            yield frame("error", message="Selected model is at capacity. Please try a different model.")
            yield frame(
                "response.failed",
                response={
                    "id": "resp_first",
                    "status": "failed",
                    "error": {"message": "Selected model is at capacity. Please try a different model."},
                },
            )

    monkeypatch.setattr(proxy, "_SSE_PREFETCH_SECONDS", 0.01)
    seed_account("delayed-capacity-first")
    seed_account("delayed-capacity-second")
    secret = make_user("delayed-capacity-user")
    completed = frame(
        "response.completed",
        response={
            "id": "resp_second",
            "status": "completed",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )
    with respx.mock:
        respx.route(host="testserver").pass_through()
        route = respx.post("https://chatgpt.com/backend-api/codex/responses").mock(
            side_effect=[
                httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=DelayedFailure()),
                httpx.Response(200, headers={"content-type": "text/event-stream"}, content=completed),
            ]
        )
        response = client.post(
            "/api/v1/responses",
            headers={"Authorization": f"Bearer {secret}"},
            json={"model": "gpt-6.1-sol", "input": "test", "stream": True},
        )
    assert response.status_code == 200
    assert route.call_count == 2
    assert "resp_second" in response.text
    assert "Selected model is at capacity" not in response.text


def test_completed_event_outcome_does_not_require_redundant_status(monkeypatch):
    from app.utils.usage import StreamUsageAccumulator

    accumulator = StreamUsageAccumulator()
    accumulator.feed(frame("response.completed", response={"id": "resp_real", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    recorded = []
    monkeypatch.setattr(proxy, "_emit_event", lambda request, event_type, **kwargs: recorded.append(event_type))
    proxy._emit_stream_outcome(None, accumulator, True, None, None, None, None, 200)
    assert recorded == ["response.completed"]


def test_completed_response_is_success_when_client_closes_before_eof(monkeypatch):
    from app.utils.usage import StreamUsageAccumulator

    accumulator = StreamUsageAccumulator()
    accumulator.feed(frame("response.completed", response={"id": "resp_real", "status": "completed"}))
    recorded = []
    monkeypatch.setattr(proxy, "_emit_event", lambda request, event_type, **kwargs: recorded.append(event_type))
    proxy._emit_stream_outcome(None, accumulator, False, None, None, None, None, 200)
    assert recorded == ["response.completed"]
