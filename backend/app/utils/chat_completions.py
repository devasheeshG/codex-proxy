"""OpenAI Chat Completions compatibility over the Codex Responses API.

The ChatGPT Codex subscription backend exposes Responses, not Chat
Completions.  This module performs the protocol translation at our public API
boundary while keeping routing, quota accounting, and failover in the existing
Responses proxy path.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class ChatCompletionTranslationError(ValueError):
    """A client request cannot be represented safely by Responses."""

    def __init__(self, message: str, *, param: Optional[str] = None, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.param = param
        self.code = code

    def error_body(self) -> Dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": "invalid_request_error",
                "param": self.param,
                "code": self.code,
            }
        }


@dataclass(frozen=True)
class ChatRequestMetadata:
    stream: bool
    include_usage: bool
    restore_tool_names: Mapping[str, str] = field(default_factory=dict)


def _require_mapping(value: object, param: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ChatCompletionTranslationError(f"'{param}' must be an object.", param=param)
    return value


def _shorten_tool_names(names: Sequence[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return stable unique <=64-character names and their reverse mapping."""
    forward: Dict[str, str] = {}
    reverse: Dict[str, str] = {}
    used: set[str] = set()
    for original in names:
        if original in forward:
            continue
        if len(original) <= 64:
            base = original
        elif original.startswith("mcp__") and "__" in original[5:]:
            base = f"mcp__{original.rsplit('__', 1)[-1]}"[:64]
        else:
            base = original[:64]
        candidate = base
        suffix = 1
        while candidate in used:
            marker = f"_{suffix}"
            candidate = f"{base[: 64 - len(marker)]}{marker}"
            suffix += 1
        used.add(candidate)
        forward[original] = candidate
        reverse[candidate] = original
    return forward, reverse


def _tool_names(body: Mapping[str, Any]) -> List[str]:
    names: List[str] = []
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            function = tool.get("function")
            if tool.get("type") == "function" and isinstance(function, Mapping) and isinstance(function.get("name"), str):
                names.append(function["name"])
    functions = body.get("functions")
    if isinstance(functions, list):
        names.extend(function["name"] for function in functions if isinstance(function, Mapping) and isinstance(function.get("name"), str))
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, Mapping) or not isinstance(message.get("tool_calls"), list):
                continue
            for call in message["tool_calls"]:
                function = call.get("function") if isinstance(call, Mapping) else None
                if isinstance(function, Mapping) and isinstance(function.get("name"), str):
                    names.append(function["name"])
    return names


def _text_part(text: object, role: str, param: str) -> Dict[str, Any]:
    if not isinstance(text, str):
        raise ChatCompletionTranslationError("Text content must be a string.", param=param)
    return {"type": "output_text" if role == "assistant" else "input_text", "text": text}


def _image_part(part: Mapping[str, Any], param: str) -> Dict[str, Any]:
    image = part.get("image_url")
    if isinstance(image, str):
        url = image
        detail = None
    elif isinstance(image, Mapping):
        url = image.get("url")
        detail = image.get("detail")
    else:
        url = None
        detail = None
    if not isinstance(url, str) or not url:
        raise ChatCompletionTranslationError("Image content requires image_url.url.", param=param)
    result: Dict[str, Any] = {"type": "input_image", "image_url": url}
    if detail is not None:
        if detail not in {"auto", "low", "high"}:
            raise ChatCompletionTranslationError("Image detail must be 'auto', 'low', or 'high'.", param=param)
        result["detail"] = detail
    return result


def _file_part(part: Mapping[str, Any], param: str) -> Dict[str, Any]:
    file = _require_mapping(part.get("file"), f"{param}.file")
    result: Dict[str, Any] = {"type": "input_file"}
    for source, target in (("file_id", "file_id"), ("file_data", "file_data"), ("filename", "filename")):
        if isinstance(file.get(source), str) and file[source]:
            result[target] = file[source]
    if not any(key in result for key in ("file_id", "file_data")):
        raise ChatCompletionTranslationError("File content requires file_id or file_data.", param=param)
    return result


def _message_content(role: str, content: object, param: str) -> List[Dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [_text_part(content, role, param)] if content else []
    if not isinstance(content, list):
        raise ChatCompletionTranslationError("Message content must be a string, an array, or null.", param=param)
    converted: List[Dict[str, Any]] = []
    for index, raw_part in enumerate(content):
        part_param = f"{param}.{index}"
        part = _require_mapping(raw_part, part_param)
        part_type = part.get("type")
        if part_type in {"text", "input_text", "output_text"}:
            converted.append(_text_part(part.get("text"), role, f"{part_param}.text"))
        elif part_type in {"image_url", "input_image"}:
            if role not in {"user", "tool"}:
                raise ChatCompletionTranslationError("Images are only supported in user or tool messages.", param=part_param)
            if part_type == "input_image":
                normalized = {
                    "image_url": {
                        "url": part.get("image_url"),
                        "detail": part.get("detail"),
                    }
                }
                converted.append(_image_part(normalized, part_param))
            else:
                converted.append(_image_part(part, part_param))
        elif part_type == "file":
            if role != "user":
                raise ChatCompletionTranslationError("Files are only supported in user messages.", param=part_param)
            converted.append(_file_part(part, part_param))
        elif part_type == "input_audio":
            raise ChatCompletionTranslationError("Audio input is not supported by the Codex subscription backend.", param=part_param)
        else:
            raise ChatCompletionTranslationError(f"Unsupported message content type '{part_type}'.", param=part_param)
    return converted


def _tool_output(content: object, param: str) -> object:
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content
        if not isinstance(decoded, list):
            return content
        content = decoded
    if isinstance(content, list):
        return _message_content("tool", content, param)
    if content is None:
        return ""
    if isinstance(content, (Mapping, int, float, bool)):
        return json.dumps(content, separators=(",", ":"))
    raise ChatCompletionTranslationError("Tool output must be text or supported content parts.", param=param)


def _convert_messages(messages: object, tool_names: Mapping[str, str]) -> List[Dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise ChatCompletionTranslationError("'messages' must be a non-empty array.", param="messages")
    result: List[Dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    generated_calls = 0
    for index, raw_message in enumerate(messages):
        param = f"messages.{index}"
        message = _require_mapping(raw_message, param)
        role = message.get("role")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ChatCompletionTranslationError(f"Unsupported message role '{role}'.", param=f"{param}.role")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ChatCompletionTranslationError("Tool messages require tool_call_id.", param=f"{param}.tool_call_id")
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _tool_output(message.get("content"), f"{param}.content"),
                }
            )
            continue

        response_role = "developer" if role == "system" else role
        content = _message_content(role, message.get("content"), f"{param}.content")
        refusal = message.get("refusal")
        if role == "assistant" and isinstance(refusal, str) and refusal:
            content.append({"type": "output_text", "text": refusal})
        if role != "assistant" or content:
            result.append({"type": "message", "role": response_role, "content": content})

        tool_calls = message.get("tool_calls")
        if tool_calls is None:
            continue
        if role != "assistant" or not isinstance(tool_calls, list):
            raise ChatCompletionTranslationError("tool_calls must be an array on an assistant message.", param=f"{param}.tool_calls")
        for tool_index, raw_call in enumerate(tool_calls):
            call_param = f"{param}.tool_calls.{tool_index}"
            call = _require_mapping(raw_call, call_param)
            if call.get("type", "function") != "function":
                raise ChatCompletionTranslationError("Only function tool calls are supported.", param=f"{call_param}.type")
            function = _require_mapping(call.get("function"), f"{call_param}.function")
            name = function.get("name")
            arguments = function.get("arguments", "")
            if not isinstance(name, str) or not name:
                raise ChatCompletionTranslationError("Function calls require a name.", param=f"{call_param}.function.name")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, separators=(",", ":"))
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                call_id = f"call_missing_{index}_{generated_calls}"
                generated_calls += 1
            if call_id in seen_call_ids:
                raise ChatCompletionTranslationError("Duplicate tool call id.", param=f"{call_param}.id")
            seen_call_ids.add(call_id)
            result.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": tool_names.get(name, name[:64]),
                    "arguments": arguments,
                }
            )
    return result


def _convert_tools(body: Mapping[str, Any], tool_names: Mapping[str, str]) -> Optional[List[Dict[str, Any]]]:
    raw_tools = body.get("tools")
    if raw_tools is None and body.get("functions") is not None:
        raw_tools = [{"type": "function", "function": function} for function in body["functions"]]
    if raw_tools is None:
        return None
    if not isinstance(raw_tools, list):
        raise ChatCompletionTranslationError("'tools' must be an array.", param="tools")
    tools: List[Dict[str, Any]] = []
    for index, raw_tool in enumerate(raw_tools):
        param = f"tools.{index}"
        tool = _require_mapping(raw_tool, param)
        if tool.get("type") != "function":
            raise ChatCompletionTranslationError("Only function tools are supported by Chat Completions.", param=f"{param}.type")
        function = _require_mapping(tool.get("function"), f"{param}.function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ChatCompletionTranslationError("Function tools require a name.", param=f"{param}.function.name")
        converted: Dict[str, Any] = {"type": "function", "name": tool_names.get(name, name[:64])}
        for field_name in ("description", "parameters", "strict"):
            if field_name in function:
                converted[field_name] = function[field_name]
        tools.append(converted)
    return tools


def _convert_tool_choice(value: object, tool_names: Mapping[str, str]) -> object:
    if isinstance(value, str):
        if value not in {"none", "auto", "required"}:
            raise ChatCompletionTranslationError("Invalid tool_choice.", param="tool_choice")
        return value
    choice = _require_mapping(value, "tool_choice")
    if choice.get("type") != "function":
        raise ChatCompletionTranslationError("Only function tool_choice is supported.", param="tool_choice.type")
    function = _require_mapping(choice.get("function"), "tool_choice.function")
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ChatCompletionTranslationError("tool_choice requires a function name.", param="tool_choice.function.name")
    return {"type": "function", "name": tool_names.get(name, name[:64])}


def _convert_response_format(value: object) -> Dict[str, Any]:
    response_format = _require_mapping(value, "response_format")
    format_type = response_format.get("type")
    if format_type == "text":
        return {"type": "text"}
    if format_type == "json_object":
        return {"type": "json_object"}
    if format_type != "json_schema":
        raise ChatCompletionTranslationError("Unsupported response_format type.", param="response_format.type")
    schema = _require_mapping(response_format.get("json_schema"), "response_format.json_schema")
    if not isinstance(schema.get("name"), str) or not schema["name"]:
        raise ChatCompletionTranslationError("json_schema requires a name.", param="response_format.json_schema.name")
    converted = {"type": "json_schema", "name": schema["name"]}
    for field_name in ("description", "schema", "strict"):
        if field_name in schema:
            converted[field_name] = schema[field_name]
    return converted


def request_to_responses(body: object) -> Tuple[Dict[str, Any], ChatRequestMetadata]:
    """Translate one Chat Completions request into a Codex Responses request."""
    request = _require_mapping(body, "body")
    model = request.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ChatCompletionTranslationError("'model' is required.", param="model")
    stream = request.get("stream", False)
    if not isinstance(stream, bool):
        raise ChatCompletionTranslationError("'stream' must be a boolean.", param="stream")
    stream_options = request.get("stream_options")
    include_usage = False
    if stream_options is not None:
        options = _require_mapping(stream_options, "stream_options")
        include_usage = options.get("include_usage") is True

    n = request.get("n", 1)
    if n not in {None, 1}:
        raise ChatCompletionTranslationError("Only n=1 is supported by the Codex Responses backend.", param="n")
    stop = request.get("stop")
    if stop is not None and stop != []:
        raise ChatCompletionTranslationError("Stop sequences cannot be represented by the Responses API.", param="stop")
    if request.get("logprobs") is True or request.get("top_logprobs") not in {None, 0}:
        raise ChatCompletionTranslationError("Log probabilities are not supported by this compatibility endpoint.", param="logprobs")
    modalities = request.get("modalities")
    if modalities is not None and modalities != ["text"] and modalities != ("text",):
        raise ChatCompletionTranslationError("Only text output is supported.", param="modalities")
    if request.get("audio") is not None:
        raise ChatCompletionTranslationError("Audio output is not supported.", param="audio")

    forward_names, reverse_names = _shorten_tool_names(_tool_names(request))
    converted: Dict[str, Any] = {
        "model": model,
        "input": _convert_messages(request.get("messages"), forward_names),
        "stream": True,
        "store": False,
    }
    tools = _convert_tools(request, forward_names)
    if tools is not None:
        converted["tools"] = tools
    if request.get("tool_choice") is not None:
        converted["tool_choice"] = _convert_tool_choice(request["tool_choice"], forward_names)
    elif request.get("function_call") is not None:
        legacy_choice = request["function_call"]
        if isinstance(legacy_choice, Mapping) and isinstance(legacy_choice.get("name"), str):
            name = legacy_choice["name"]
            converted["tool_choice"] = {"type": "function", "name": forward_names.get(name, name[:64])}
        elif isinstance(legacy_choice, str) and legacy_choice in {"none", "auto"}:
            converted["tool_choice"] = legacy_choice
        else:
            raise ChatCompletionTranslationError("Invalid legacy function_call.", param="function_call")
    if "parallel_tool_calls" in request:
        converted["parallel_tool_calls"] = bool(request["parallel_tool_calls"])
    reasoning_effort = request.get("reasoning_effort")
    if reasoning_effort is not None:
        if not isinstance(reasoning_effort, str):
            raise ChatCompletionTranslationError("reasoning_effort must be a string.", param="reasoning_effort")
        converted["reasoning"] = {"effort": reasoning_effort}
    if "service_tier" in request:
        converted["service_tier"] = request["service_tier"]
    if "response_format" in request:
        converted["text"] = {"format": _convert_response_format(request["response_format"])}
    if "verbosity" in request:
        converted.setdefault("text", {})["verbosity"] = request["verbosity"]
    for field_name in ("metadata", "prompt_cache_key", "safety_identifier"):
        if field_name in request:
            converted[field_name] = request[field_name]

    return converted, ChatRequestMetadata(
        stream=stream,
        include_usage=include_usage,
        restore_tool_names=reverse_names,
    )


def _chat_id(response_id: object) -> str:
    if isinstance(response_id, str) and response_id:
        if response_id.startswith("resp_"):
            return f"chatcmpl-{response_id[5:]}"
        if response_id.startswith("chatcmpl-"):
            return response_id
        return f"chatcmpl-{response_id}"
    return "chatcmpl-codex-proxy"


def _usage_block(value: object) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    prompt = int(value.get("input_tokens") or value.get("prompt_tokens") or 0)
    completion = int(value.get("output_tokens") or value.get("completion_tokens") or 0)
    input_details = value.get("input_tokens_details") or value.get("prompt_tokens_details")
    output_details = value.get("output_tokens_details") or value.get("completion_tokens_details")
    input_details = input_details if isinstance(input_details, Mapping) else {}
    output_details = output_details if isinstance(output_details, Mapping) else {}
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(value.get("total_tokens") or prompt + completion),
        "prompt_tokens_details": {
            "cached_tokens": int(input_details.get("cached_tokens") or input_details.get("cached_input_tokens") or 0),
        },
        "completion_tokens_details": {
            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        },
    }
    return usage


def _output_parts(
    response: Mapping[str, Any],
    restore_names: Mapping[str, str],
) -> Tuple[str, Optional[str], List[Dict[str, Any]], str, str]:
    text_parts: List[str] = []
    reasoning_parts: List[str] = []
    refusals: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if item_type == "message" and isinstance(item.get("content"), list):
                for part in item["content"]:
                    if not isinstance(part, Mapping):
                        continue
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                    elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                        refusals.append(part["refusal"])
            elif item_type == "function_call":
                name = item.get("name") if isinstance(item.get("name"), str) else ""
                call_id = item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"
                arguments = item.get("arguments", "")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, separators=(",", ":"))
                tool_calls.append(
                    {
                        "id": str(call_id),
                        "type": "function",
                        "function": {"name": restore_names.get(name, name), "arguments": arguments},
                    }
                )
            elif item_type == "reasoning":
                summaries = item.get("summary")
                if isinstance(summaries, list):
                    reasoning_parts.extend(part["text"] for part in summaries if isinstance(part, Mapping) and isinstance(part.get("text"), str))
    finish_reason = "tool_calls" if tool_calls else "stop"
    if response.get("status") == "incomplete":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, Mapping) else None
        finish_reason = "content_filter" if reason == "content_filter" else "length"
    return "".join(text_parts), "".join(refusals) or None, tool_calls, "".join(reasoning_parts), finish_reason


def response_to_chat_completion(response: object, metadata: ChatRequestMetadata) -> Dict[str, Any]:
    """Translate a terminal Responses object to a Chat Completion object."""
    source = _require_mapping(response, "response")
    if source.get("status") == "failed":
        error = source.get("error")
        message = error.get("message") if isinstance(error, Mapping) else None
        raise ChatCompletionTranslationError(message or "The upstream Responses request failed.")
    text, refusal, tool_calls, reasoning, finish_reason = _output_parts(source, metadata.restore_tool_names)
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": text if text else None,
        "refusal": refusal,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning:
        message["reasoning_content"] = reasoning
    result: Dict[str, Any] = {
        "id": _chat_id(source.get("id")),
        "object": "chat.completion",
        "created": int(source.get("created_at") or time.time()),
        "model": source.get("model") or "unknown",
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": _usage_block(source.get("usage")),
    }
    for field_name in ("service_tier", "system_fingerprint"):
        if source.get(field_name) is not None:
            result[field_name] = source[field_name]
    return result


class ChatCompletionStreamTranslator:
    """Incrementally convert Responses SSE frames to Chat Completions SSE."""

    def __init__(self, fallback_model: Optional[str], metadata: ChatRequestMetadata) -> None:
        self._buffer = ""
        self._id = "chatcmpl-codex-proxy"
        self._created = int(time.time())
        self._model = fallback_model or "unknown"
        self._metadata = metadata
        self._role_sent = False
        self._done = False
        self._terminal_seen = False
        self._tool_indices: Dict[str, int] = {}
        self._tool_argument_lengths: Dict[str, int] = {}

    def _chunk(self, delta: Mapping[str, Any], finish_reason: Optional[str] = None) -> bytes:
        body: Dict[str, Any] = {
            "id": self._id,
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": self._model,
            "choices": [{"index": 0, "delta": dict(delta), "logprobs": None, "finish_reason": finish_reason}],
        }
        if self._metadata.include_usage:
            body["usage"] = None
        return f"data: {json.dumps(body, separators=(',', ':'))}\n\n".encode()

    @staticmethod
    def _data(value: Mapping[str, Any]) -> bytes:
        return f"data: {json.dumps(value, separators=(',', ':'))}\n\n".encode()

    def _ensure_role(self) -> List[bytes]:
        if self._role_sent:
            return []
        self._role_sent = True
        return [self._chunk({"role": "assistant", "content": ""})]

    def _tool_key(self, event: Mapping[str, Any], item: Optional[Mapping[str, Any]] = None) -> str:
        item = item or {}
        value = event.get("item_id") or item.get("id") or item.get("call_id")
        return str(value or f"tool_{len(self._tool_indices)}")

    def _tool_delta(self, event: Mapping[str, Any], item: Mapping[str, Any]) -> List[bytes]:
        key = self._tool_key(event, item)
        index = self._tool_indices.setdefault(key, len(self._tool_indices))
        call_id = str(item.get("call_id") or item.get("id") or key)
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        name = self._metadata.restore_tool_names.get(name, name)
        arguments = item.get("arguments") if isinstance(item.get("arguments"), str) else ""
        self._tool_argument_lengths.setdefault(key, 0)
        delta = {
            "tool_calls": [
                {
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ]
        }
        self._tool_argument_lengths[key] = len(arguments)
        return self._ensure_role() + [self._chunk(delta)]

    def _consume_event(self, event: Mapping[str, Any]) -> List[bytes]:
        event_type = event.get("type")
        if event_type == "response.created":
            response = event.get("response")
            if isinstance(response, Mapping):
                self._id = _chat_id(response.get("id"))
                self._created = int(response.get("created_at") or self._created)
                self._model = str(response.get("model") or self._model)
            return self._ensure_role()
        if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
            return self._ensure_role() + [self._chunk({"content": event["delta"]})]
        if event_type == "response.refusal.delta" and isinstance(event.get("delta"), str):
            return self._ensure_role() + [self._chunk({"refusal": event["delta"]})]
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"} and isinstance(event.get("delta"), str):
            return self._ensure_role() + [self._chunk({"reasoning_content": event["delta"]})]
        if event_type == "response.output_item.added" and isinstance(event.get("item"), Mapping):
            item = event["item"]
            if item.get("type") == "function_call":
                return self._tool_delta(event, item)
            return []
        if event_type == "response.function_call_arguments.delta" and isinstance(event.get("delta"), str):
            key = self._tool_key(event)
            index = self._tool_indices.setdefault(key, len(self._tool_indices))
            self._tool_argument_lengths[key] = self._tool_argument_lengths.get(key, 0) + len(event["delta"])
            delta = {"tool_calls": [{"index": index, "function": {"arguments": event["delta"]}}]}
            return self._ensure_role() + [self._chunk(delta)]
        if event_type == "response.output_item.done" and isinstance(event.get("item"), Mapping):
            item = event["item"]
            if item.get("type") == "function_call":
                key = self._tool_key(event, item)
                if key not in self._tool_indices:
                    return self._tool_delta(event, item)
                arguments = item.get("arguments") if isinstance(item.get("arguments"), str) else ""
                emitted = self._tool_argument_lengths.get(key, 0)
                if len(arguments) > emitted:
                    index = self._tool_indices[key]
                    remainder = arguments[emitted:]
                    self._tool_argument_lengths[key] = len(arguments)
                    return [self._chunk({"tool_calls": [{"index": index, "function": {"arguments": remainder}}]})]
            return []
        if event_type in {"response.completed", "response.incomplete"}:
            response = event.get("response")
            response = response if isinstance(response, Mapping) else {}
            self._id = _chat_id(response.get("id") or self._id)
            self._created = int(response.get("created_at") or self._created)
            self._model = str(response.get("model") or self._model)
            _, _, tool_calls, _, finish_reason = _output_parts(response, self._metadata.restore_tool_names)
            if tool_calls:
                finish_reason = "tool_calls"
            frames = self._ensure_role() + [self._chunk({}, finish_reason)]
            if self._metadata.include_usage:
                usage = _usage_block(response.get("usage"))
                frames.append(
                    self._data(
                        {
                            "id": self._id,
                            "object": "chat.completion.chunk",
                            "created": self._created,
                            "model": self._model,
                            "choices": [],
                            "usage": usage,
                        }
                    )
                )
            frames.append(b"data: [DONE]\n\n")
            self._terminal_seen = True
            self._done = True
            return frames
        if event_type in {"response.failed", "error"}:
            response = event.get("response")
            error = response.get("error") if isinstance(response, Mapping) else event.get("error")
            error = error if isinstance(error, Mapping) else {}
            message = error.get("message") or event.get("message") or "The upstream Responses request failed."
            self._done = True
            self._terminal_seen = True
            return [
                self._data({"error": {"message": message, "type": error.get("type") or "upstream_error", "code": error.get("code")}}),
                b"data: [DONE]\n\n",
            ]
        return []

    def _consume_frame(self, frame: str) -> List[bytes]:
        data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
        if not data or data == "[DONE]" or self._done:
            return []
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return []
        return self._consume_event(event) if isinstance(event, Mapping) else []

    def feed(self, chunk: bytes) -> List[bytes]:
        self._buffer += chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
        output: List[bytes] = []
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            output.extend(self._consume_frame(frame))
        return output

    def finish(self) -> List[bytes]:
        output: List[bytes] = []
        if self._buffer.strip():
            output.extend(self._consume_frame(self._buffer.strip()))
            self._buffer = ""
        if not self._terminal_seen:
            output.append(
                self._data(
                    {
                        "error": {
                            "message": "Codex upstream ended without a terminal Responses API event.",
                            "type": "proxy_error",
                            "code": "incomplete_upstream_stream",
                        }
                    }
                )
            )
            output.append(b"data: [DONE]\n\n")
            self._done = True
        return output
