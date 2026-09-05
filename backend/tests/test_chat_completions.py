import json

import httpx
import pytest
import respx

from app.utils import chat_completions

CODEX_RESPONSES = "https://chatgpt.com/backend-api/codex/responses"


def _metadata(*, stream=False, include_usage=False, restore=None):
    return chat_completions.ChatRequestMetadata(
        stream=stream,
        include_usage=include_usage,
        restore_tool_names=restore or {},
    )


def test_request_translation_preserves_messages_images_tools_and_json_schema():
    long_name = f"mcp__filesystem__{'read_file_' * 8}"
    request = {
        "model": "gpt-5.6-sol",
        "messages": [
            {"role": "system", "content": "Be precise."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe these."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png", "detail": "high"}},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
            {
                "role": "assistant",
                "content": "I will inspect it.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": long_name, "arguments": '{"path":"a.png"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": json.dumps(
                    [
                        {"type": "text", "text": "pixels"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
                    ]
                ),
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": long_name,
                    "description": "Read an image",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "strict": True,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": long_name}},
        "parallel_tool_calls": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": {"type": "object"}},
        },
        "reasoning_effort": "high",
        "service_tier": "priority",
        "max_completion_tokens": 100,
        "temperature": 0.2,
    }

    translated, metadata = chat_completions.request_to_responses(request)

    assert translated["model"] == "gpt-5.6-sol"
    assert translated["stream"] is True
    assert translated["store"] is False
    assert "max_output_tokens" not in translated
    assert "temperature" not in translated
    assert translated["input"][0] == {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": "Be precise."}],
    }
    assert translated["input"][1]["content"][1] == {
        "type": "input_image",
        "image_url": "https://example.com/a.png",
        "detail": "high",
    }
    assert translated["input"][1]["content"][2]["image_url"].startswith("data:image/png")
    function_call = translated["input"][3]
    assert function_call["type"] == "function_call"
    assert function_call["call_id"] == "call_1"
    assert len(function_call["name"]) <= 64
    assert translated["input"][4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": [
            {"type": "input_text", "text": "pixels"},
            {"type": "input_image", "image_url": "data:image/png;base64,BBBB"},
        ],
    }
    assert translated["tools"][0]["name"] == function_call["name"]
    assert translated["tool_choice"] == {"type": "function", "name": function_call["name"]}
    assert translated["text"]["format"] == {
        "type": "json_schema",
        "name": "answer",
        "strict": True,
        "schema": {"type": "object"},
    }
    assert translated["reasoning"] == {"effort": "high"}
    assert metadata.restore_tool_names[function_call["name"]] == long_name


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"n": 2}, "n"),
        ({"stop": ["END"]}, "stop"),
        ({"logprobs": True}, "logprobs"),
        ({"modalities": ["text", "audio"]}, "modalities"),
        ({"audio": {"voice": "alloy"}}, "audio"),
    ],
)
def test_request_translation_fails_closed_for_unsupported_semantics(change, param):
    request = {"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}], **change}
    with pytest.raises(chat_completions.ChatCompletionTranslationError) as exc:
        chat_completions.request_to_responses(request)
    assert exc.value.param == param


def test_request_translation_rejects_audio_input_and_accepts_legacy_functions():
    with pytest.raises(chat_completions.ChatCompletionTranslationError, match="Audio input"):
        chat_completions.request_to_responses(
            {
                "model": "gpt-5.6-sol",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}],
                    }
                ],
            }
        )

    translated, _ = chat_completions.request_to_responses(
        {
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hello"}],
            "functions": [{"name": "lookup", "parameters": {"type": "object"}}],
            "function_call": {"name": "lookup"},
        }
    )
    assert translated["tools"] == [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]
    assert translated["tool_choice"] == {"type": "function", "name": "lookup"}


def test_terminal_response_translation_preserves_text_tools_reasoning_and_usage():
    response = {
        "id": "resp_123",
        "object": "response",
        "created_at": 123456,
        "status": "completed",
        "model": "gpt-5.6-sol",
        "service_tier": "priority",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Checked the image."}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I need one tool."}],
            },
            {"type": "function_call", "call_id": "call_1", "name": "short", "arguments": '{"x":1}'},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 6,
            "total_tokens": 16,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }

    translated = chat_completions.response_to_chat_completion(
        response,
        _metadata(restore={"short": "original_tool_name"}),
    )

    assert translated["id"] == "chatcmpl-123"
    assert translated["object"] == "chat.completion"
    assert translated["choices"][0]["finish_reason"] == "tool_calls"
    message = translated["choices"][0]["message"]
    assert message["content"] == "I need one tool."
    assert message["reasoning_content"] == "Checked the image."
    assert message["tool_calls"][0] == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "original_tool_name", "arguments": '{"x":1}'},
    }
    assert translated["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 6,
        "total_tokens": 16,
        "prompt_tokens_details": {"cached_tokens": 4},
        "completion_tokens_details": {"reasoning_tokens": 2},
    }
    assert translated["service_tier"] == "priority"


def test_stream_translation_handles_split_frames_text_tools_usage_and_done():
    metadata = _metadata(stream=True, include_usage=True, restore={"short": "original_tool"})
    translator = chat_completions.ChatCompletionStreamTranslator("fallback", metadata)
    terminal = {
        "id": "resp_stream",
        "created_at": 222,
        "model": "gpt-5.6-sol",
        "status": "completed",
        "output": [{"type": "function_call", "id": "item_1", "call_id": "call_1", "name": "short", "arguments": '{"x":1}'}],
        "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    }
    events = [
        {"type": "response.created", "response": {"id": "resp_stream", "created_at": 222, "model": "gpt-5.6-sol"}},
        {"type": "response.output_text.delta", "delta": "Working "},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "id": "item_1", "call_id": "call_1", "name": "short", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": '{"x":'},
        {"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": "1}"},
        {"type": "response.completed", "response": terminal},
    ]
    raw = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
    frames = []
    for offset in range(0, len(raw), 17):
        frames.extend(translator.feed(raw[offset : offset + 17]))
    frames.extend(translator.finish())
    payloads = [frame.decode().removeprefix("data: ").strip() for frame in frames]

    assert payloads[-1] == "[DONE]"
    decoded = [json.loads(payload) for payload in payloads[:-1]]
    assert decoded[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert any(item["choices"][0]["delta"].get("content") == "Working " for item in decoded if item["choices"])
    tool_chunks = [
        item["choices"][0]["delta"]["tool_calls"][0] for item in decoded if item["choices"] and "tool_calls" in item["choices"][0]["delta"]
    ]
    assert tool_chunks[0]["function"]["name"] == "original_tool"
    assert "".join(chunk["function"].get("arguments", "") for chunk in tool_chunks) == '{"x":1}'
    assert decoded[-2]["choices"][0]["finish_reason"] == "tool_calls"
    assert decoded[-1]["choices"] == []
    assert decoded[-1]["usage"]["total_tokens"] == 10


@respx.mock
def test_chat_completions_endpoint_translates_nonstreaming_images_and_tool_response(
    client,
    admin_headers,
    seed_account,
    make_user,
):
    seed_account("chat-nonstream")
    key = make_user("chat-nonstream-user")
    completed = {
        "id": "resp_chat",
        "object": "response",
        "created_at": 333,
        "status": "completed",
        "model": "gpt-5.6-sol",
        "output": [{"type": "function_call", "call_id": "call_weather", "name": "weather", "arguments": '{"city":"Delhi"}'}],
        "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
    }
    sse = f"data: {json.dumps({'type': 'response.completed', 'response': completed})}\n\n"

    respx.route(host="testserver").pass_through()

    def upstream(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "none"}
        assert body["input"] == [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Weather?"},
                    {"type": "input_image", "image_url": "https://example.com/map.png", "detail": "low"},
                ],
            }
        ]
        assert body["tools"][0]["name"] == "weather"
        return httpx.Response(200, headers={"content-type": "application/json"}, text=sse)

    route = respx.post(CODEX_RESPONSES).mock(side_effect=upstream)
    response = client.post(
        "/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.6-sol",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Weather?"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/map.png", "detail": "low"}},
                    ],
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "weather", "parameters": {"type": "object"}},
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert route.call_count == 1
    assert response.json()["object"] == "chat.completion"
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["id"] == "call_weather"
    record = client.get("/api/v1/stats/usage", headers=admin_headers).json()["items"][0]
    assert record["input_tokens"] == 12
    assert record["output_tokens"] == 4
    assert record["reasoning_level"] == "none"


@respx.mock
def test_chat_completions_endpoint_translates_streaming_tool_calls_and_usage(
    client,
    admin_headers,
    seed_account,
    make_user,
):
    seed_account("chat-stream")
    key = make_user("chat-stream-user")
    terminal = {
        "id": "resp_route_stream",
        "created_at": 444,
        "status": "completed",
        "model": "gpt-5.6-sol",
        "output": [{"type": "function_call", "id": "item_route", "call_id": "call_route", "name": "lookup", "arguments": '{"q":"x"}'}],
        "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
    }
    events = [
        {"type": "response.created", "response": {"id": "resp_route_stream", "created_at": 444, "model": "gpt-5.6-sol"}},
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "item_route", "call_id": "call_route", "name": "lookup", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "item_id": "item_route", "delta": '{"q":"x"}'},
        {"type": "response.completed", "response": terminal},
    ]
    sse = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, headers={"content-type": "application/json"}, text=sse))

    response = client.post(
        "/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "Look it up"}],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"object":"chat.completion.chunk"' in response.text
    assert '"finish_reason":"tool_calls"' in response.text
    assert '"choices":[],"usage":{"prompt_tokens":20' in response.text
    assert response.text.rstrip().endswith("data: [DONE]")
    assert json.loads(route.calls[0].request.content)["stream"] is True
    record = client.get("/api/v1/stats/usage", headers=admin_headers).json()["items"][0]
    assert record["input_tokens"] == 20
    assert record["output_tokens"] == 8


@respx.mock
def test_chat_completions_validation_uses_openai_error_shape_without_upstream_call(client, seed_account, make_user):
    seed_account("chat-validation")
    key = make_user("chat-validation-user")
    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={}))

    response = client.post(
        "/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}], "n": 2},
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["error"]["param"] == "n"
    assert route.call_count == 0


def test_chat_completions_requires_user_key(client):
    response = client.post(
        "/api/v1/chat/completions",
        json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 401
