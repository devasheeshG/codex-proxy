"use client";

/* Embedded archive images are arbitrary data URLs; next/image cannot optimize them. */
/* eslint-disable @next/next/no-img-element */

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { ArchiveRequestDetail } from "@/lib/types";
import { Button, Modal } from "@/components/ui";

type Role = "user" | "assistant" | "system" | "developer" | "tool" | "tools" | "other";
type ImageSource = { src: string; alt: string };
type Card = { role: Role; label?: string; content: string; images?: ImageSource[] };

const ROLE_STYLES: Record<Role, { accent: string; badge: string }> = {
    user: { accent: "border-brand-400/40", badge: "bg-brand-500/15 text-brand-300" },
    assistant: { accent: "border-good-500/40", badge: "bg-good-500/15 text-good-400" },
    system: { accent: "border-warn-500/40", badge: "bg-warn-500/15 text-warn-400" },
    developer: { accent: "border-purple-400/40", badge: "bg-purple-500/15 text-purple-300" },
    tool: { accent: "border-cyan-400/40", badge: "bg-cyan-500/15 text-cyan-300" },
    tools: { accent: "border-cyan-400/40", badge: "bg-cyan-500/15 text-cyan-300" },
    other: { accent: "border-ink-600", badge: "bg-ink-700 text-fog-300" },
};

function pretty(value: unknown): string {
    if (typeof value === "string") return value;
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

function parseJson(body: string): unknown {
    try {
        return JSON.parse(body);
    } catch {
        return null;
    }
}

function formatToolContent(value: string): string {
    const parsed = parseJson(value);
    return parsed && typeof parsed === "object" ? pretty(parsed) : value;
}

function contentText(content: unknown): string {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
        return content
            .map((part) => {
                if (typeof part === "string") return part;
                if (!part || typeof part !== "object") return String(part ?? "");
                const item = part as Record<string, unknown>;
                return String(item.text ?? item.output_text ?? item.content ?? pretty(item));
            })
            .join("\n");
    }
    return content == null ? "" : pretty(content);
}

function imageSource(value: unknown): ImageSource | null {
    if (!value || typeof value !== "object") return null;
    const safe = (url: unknown): ImageSource | null => {
        if (typeof url !== "string") return null;
        if (!/^(?:https?:\/\/|data:image\/)/i.test(url)) return null;
        return { src: url, alt: "Embedded image" };
    };
    const item = value as Record<string, unknown>;
    const imageUrl = item.image_url;
    if (typeof imageUrl === "string") return safe(imageUrl);
    if (imageUrl && typeof imageUrl === "object") {
        const url = (imageUrl as Record<string, unknown>).url;
        const image = safe(url);
        if (image) return image;
    }
    const source = item.source;
    if (source && typeof source === "object") {
        const sourceRecord = source as Record<string, unknown>;
        const data = sourceRecord.data;
        const mediaType = sourceRecord.media_type;
        if (
            sourceRecord.type === "base64" &&
            typeof data === "string" &&
            typeof mediaType === "string" &&
            mediaType.startsWith("image/")
        ) {
            return { src: `data:${mediaType};base64,${data}`, alt: "Embedded image" };
        }
    }
    return safe(item.url);
}

function contentParts(content: unknown): { text: string; images: ImageSource[] } {
    if (!Array.isArray(content)) {
        const image = imageSource(content);
        return image ? { text: "", images: [image] } : { text: contentText(content), images: [] };
    }
    const images: ImageSource[] = [];
    const text = content
        .map((part) => {
            const image = imageSource(part);
            if (image) {
                images.push(image);
                return "";
            }
            if (typeof part === "string") return part;
            if (!part || typeof part !== "object") return String(part ?? "");
            const item = part as Record<string, unknown>;
            return String(item.text ?? item.output_text ?? item.content ?? "");
        })
        .filter(Boolean)
        .join("\n");
    return { text, images };
}

function normalizeRole(role: unknown): Role {
    const value = String(role ?? "other").toLowerCase();
    if (["user", "assistant", "system", "developer"].includes(value)) return value as Role;
    if (["tool", "tools", "function", "function_call", "tool_call"].includes(value)) return "tool";
    return "other";
}

function messageCard(value: Record<string, unknown>, fallbackRole: Role = "other"): Card | null {
    const role = normalizeRole(value.role ?? value.type ?? fallbackRole);
    const content = value.content ?? value.text ?? value.output_text ?? value.arguments;
    if (
        content == null &&
        !imageSource(value) &&
        value.type !== "function_call" &&
        value.type !== "tool_call"
    )
        return null;
    const parts = contentParts(content ?? value);
    return {
        role,
        label: typeof value.name === "string" ? value.name : undefined,
        content: parts.text,
        images: parts.images,
    };
}

function extractRequestCards(value: unknown): Card[] {
    if (!value || typeof value !== "object") return [];
    const root = value as Record<string, unknown>;
    const cards: Card[] = [];
    const input = root.input ?? root.messages ?? root.prompt;
    if (typeof input === "string") cards.push({ role: "user", content: input });
    if (Array.isArray(input)) {
        for (const item of input) {
            if (typeof item === "string") cards.push({ role: "user", content: item });
            else if (item && typeof item === "object") {
                const card = messageCard(item as Record<string, unknown>, "user");
                if (card) cards.push(card);
            }
        }
    }
    if (typeof root.instructions === "string")
        cards.unshift({ role: "developer", label: "Instructions", content: root.instructions });
    if (Array.isArray(root.tools) && root.tools.length)
        cards.push({
            role: "tools",
            label: `${root.tools.length} definitions`,
            content: pretty(root.tools),
        });
    return cards;
}

function extractResponseCards(body: string): Card[] {
    const parsed = parseJson(body);
    if (parsed && typeof parsed === "object") {
        const root = parsed as Record<string, unknown>;
        const output = root.output ?? root.choices;
        if (Array.isArray(output)) {
            const cards = output.flatMap((item) => {
                if (!item || typeof item !== "object") return [];
                const record = item as Record<string, unknown>;
                if (Array.isArray(record.content)) {
                    return record.content
                        .map((part) =>
                            part && typeof part === "object"
                                ? messageCard({
                                      ...(part as Record<string, unknown>),
                                      role: record.role ?? record.type,
                                  })
                                : null,
                        )
                        .filter((card): card is Card => card !== null);
                }
                const card = messageCard(record, normalizeRole(record.role ?? "assistant"));
                return card ? [card] : [];
            });
            if (cards.length) return cards;
        }
        const single = messageCard(root, "assistant");
        if (single) return [single];
    }
    const records: Array<{ event: string; data: Record<string, unknown> }> = [];
    let eventName = "message";
    let dataLines: string[] = [];
    const flush = () => {
        if (!dataLines.length) return;
        const parsedEvent = parseJson(dataLines.join("\n"));
        if (parsedEvent && typeof parsedEvent === "object")
            records.push({ event: eventName, data: parsedEvent as Record<string, unknown> });
        eventName = "message";
        dataLines = [];
    };
    for (const line of body.split(/\r?\n/)) {
        if (!line.trim()) {
            flush();
            continue;
        }
        if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
            continue;
        }
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    flush();
    const assistant: string[] = [];
    const tools = new Map<string, { label: string; content: string }>();
    const status: string[] = [];
    for (const record of records) {
        const type = String(record.data.type ?? record.event);
        const delta = record.data.delta;
        if (type.includes("output_text.delta") && typeof delta === "string") {
            assistant.push(delta);
            continue;
        }
        if (type.includes("function_call_arguments.delta") && typeof delta === "string") {
            const id = String(record.data.item_id ?? record.data.output_index ?? "tool");
            const previous = tools.get(id) ?? { label: "Tool call", content: "" };
            tools.set(id, { ...previous, content: previous.content + delta });
            continue;
        }
        const item = record.data.item;
        if (item && typeof item === "object") {
            const itemRecord = item as Record<string, unknown>;
            if (String(itemRecord.type ?? "").includes("function") || itemRecord.name) {
                tools.set(String(itemRecord.id ?? tools.size), {
                    label: typeof itemRecord.name === "string" ? itemRecord.name : "Tool call",
                    content: String(itemRecord.arguments ?? itemRecord.input ?? ""),
                });
                continue;
            }
        }
        if (type.endsWith("output_text.done") && typeof record.data.text === "string")
            assistant.push(record.data.text);
        else if (type.endsWith("response.completed")) status.push("Completed");
        else if (type.endsWith("response.failed")) status.push("Failed");
    }
    const cards: Card[] = [];
    if (assistant.join("").trim())
        cards.push({ role: "assistant", content: assistant.join(""), images: [] });
    for (const tool of tools.values())
        cards.push({
            role: "tool",
            label: tool.label,
            content: formatToolContent(tool.content),
            images: [],
        });
    for (const message of status)
        cards.push({ role: "other", label: "Response status", content: message, images: [] });
    if (!cards.length && records.length)
        cards.push({
            role: "other",
            label: "Response events",
            content: `${records.length} response events captured`,
            images: [],
        });
    return cards;
}

function MarkdownContent({ children }: { children: string }) {
    return (
        <div className="text-fog-200 text-sm leading-7">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    h1: ({ children: value }) => (
                        <h1 className="mt-4 mb-2 text-xl font-semibold">{value}</h1>
                    ),
                    h2: ({ children: value }) => (
                        <h2 className="mt-4 mb-2 text-lg font-semibold">{value}</h2>
                    ),
                    h3: ({ children: value }) => (
                        <h3 className="mt-3 mb-1 font-semibold">{value}</h3>
                    ),
                    p: ({ children: value }) => <p className="my-2">{value}</p>,
                    ul: ({ children: value }) => (
                        <ul className="my-2 list-disc space-y-1 pl-5">{value}</ul>
                    ),
                    ol: ({ children: value }) => (
                        <ol className="my-2 list-decimal space-y-1 pl-5">{value}</ol>
                    ),
                    blockquote: ({ children: value }) => (
                        <blockquote className="border-brand-400/50 text-fog-300 my-3 border-l-2 pl-3 italic">
                            {value}
                        </blockquote>
                    ),
                    a: ({ children: value, href }) => (
                        <a className="text-brand-300 underline underline-offset-2" href={href}>
                            {value}
                        </a>
                    ),
                    code: ({ children: value, className }) =>
                        className ? (
                            <code className="text-fog-200 block max-w-full overflow-x-auto rounded-md bg-black/30 p-3 font-mono text-xs break-words">
                                {value}
                            </code>
                        ) : (
                            <code className="bg-ink-700 rounded px-1 py-0.5 font-mono text-xs">
                                {value}
                            </code>
                        ),
                    pre: ({ children: value }) => (
                        <pre className="my-3 max-w-full overflow-x-auto break-words whitespace-pre-wrap">
                            {value}
                        </pre>
                    ),
                    table: ({ children: value }) => (
                        <div className="my-3 overflow-x-auto">
                            <table className="border-ink-600 min-w-full border text-left text-xs">
                                {value}
                            </table>
                        </div>
                    ),
                    th: ({ children: value }) => (
                        <th className="border-ink-600 bg-ink-800 border px-2 py-1 font-medium">
                            {value}
                        </th>
                    ),
                    td: ({ children: value }) => (
                        <td className="border-ink-700 border px-2 py-1">{value}</td>
                    ),
                }}
            >
                {children}
            </ReactMarkdown>
        </div>
    );
}

function MessageCard({ card }: { card: Card }) {
    const styles = ROLE_STYLES[card.role];
    const codeOnly = card.role === "tool" || card.role === "tools";
    return (
        <article className={`bg-ink-850 min-w-0 rounded-xl border p-4 ${styles.accent}`}>
            <header className="mb-2 flex items-center gap-2">
                <span
                    className={`rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-wide uppercase ${styles.badge}`}
                >
                    {card.role}
                </span>
                {card.label ? <span className="text-fog-400 text-xs">{card.label}</span> : null}
            </header>
            {card.images?.length ? (
                <div className="mb-3 grid gap-3 sm:grid-cols-2">
                    {card.images.map((image, index) => (
                        <figure
                            key={`${image.src.slice(0, 32)}-${index}`}
                            className="border-ink-600 bg-ink-900 overflow-hidden rounded-lg border"
                        >
                            {/* Embedded data URLs cannot use next/image without a custom loader. */}
                            <img
                                src={image.src}
                                alt={image.alt}
                                loading="lazy"
                                decoding="async"
                                className="max-h-96 w-full object-contain"
                            />
                        </figure>
                    ))}
                </div>
            ) : null}
            {card.content.trim() ? (
                codeOnly ? (
                    <pre className="text-fog-200 max-h-80 max-w-full overflow-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
                        {card.content}
                    </pre>
                ) : (
                    <MarkdownContent>{card.content}</MarkdownContent>
                )
            ) : null}
        </article>
    );
}

function BodyView({ body, side }: { body: string; side: "request" | "response" }) {
    const cards = useMemo(
        () =>
            side === "request" ? extractRequestCards(parseJson(body)) : extractResponseCards(body),
        [body, side],
    );
    const hasCards = cards.some(
        (card) => card.content.trim().length > 0 || Boolean(card.images?.length),
    );
    return (
        <div className="space-y-3">
            {hasCards ? (
                cards.map((card, index) => (
                    <MessageCard key={`${card.role}-${index}`} card={card} />
                ))
            ) : (
                <article className="bg-ink-850 border-ink-600 min-w-0 rounded-xl border p-4 break-words">
                    <span className="text-fog-400 mb-2 block text-[11px] font-semibold tracking-wide uppercase">
                        {side} content
                    </span>
                    <MarkdownContent>{body}</MarkdownContent>
                </article>
            )}
        </div>
    );
}

export function RequestCaptureOverlay({
    eventId,
    onClose,
}: {
    eventId: string;
    onClose: () => void;
}) {
    const [detail, setDetail] = useState<ArchiveRequestDetail | null>(null);
    const [requestBody, setRequestBody] = useState("");
    const [responseBody, setResponseBody] = useState("");
    const [tab, setTab] = useState<"request" | "response">("request");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        Promise.all([
            api.archiveRequest(eventId),
            api.archiveBody(eventId, "request"),
            api.archiveBody(eventId, "response"),
        ])
            .then(([next, request, response]) => {
                if (!cancelled) {
                    setDetail(next);
                    setRequestBody(request);
                    setResponseBody(response);
                }
            })
            .catch((e) => {
                if (!cancelled) {
                    const message =
                        e instanceof Error ? e.message : "Unable to load archived request.";
                    setError(
                        message.toLowerCase().includes("not found")
                            ? "This event has no archived capture. It may predate archiving or the capture may have been incomplete."
                            : message,
                    );
                }
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [eventId]);

    return (
        <Modal title="Request capture" onClose={onClose} widthClass="max-w-6xl">
            {loading ? (
                <div className="text-fog-400 flex min-h-48 items-center justify-center text-sm">
                    Loading request and response…
                </div>
            ) : error ? (
                <div className="space-y-4">
                    <p className="text-bad-400 text-sm">{error}</p>
                    <Button variant="ghost" onClick={onClose}>
                        Close
                    </Button>
                </div>
            ) : detail ? (
                <div className="space-y-4">
                    <div className="grid gap-3 text-xs sm:grid-cols-4">
                        <div>
                            <span className="text-fog-500 block uppercase">Event</span>
                            <span className="text-fog-300 font-mono">{eventId}</span>
                        </div>
                        <div>
                            <span className="text-fog-500 block uppercase">Request</span>
                            <span
                                className={
                                    detail.request_available ? "text-good-400" : "text-bad-400"
                                }
                            >
                                {detail.request_available ? "Available" : "Unavailable"}
                            </span>
                        </div>
                        <div>
                            <span className="text-fog-500 block uppercase">Response</span>
                            <span
                                className={
                                    detail.response_available ? "text-good-400" : "text-bad-400"
                                }
                            >
                                {detail.response_available ? "Available" : "Unavailable"}
                            </span>
                        </div>
                    </div>
                    <div className="border-ink-700 overflow-hidden rounded-lg border">
                        <div className="border-ink-700 flex border-b">
                            {(["request", "response"] as const).map((kind) => (
                                <button
                                    key={kind}
                                    type="button"
                                    onClick={() => setTab(kind)}
                                    className={`px-4 py-2.5 text-sm capitalize ${tab === kind ? "text-brand-300 border-brand-400 border-b-2" : "text-fog-400"}`}
                                >
                                    {kind} cards
                                </button>
                            ))}
                        </div>
                        <div className="max-h-[62vh] min-w-0 overflow-x-hidden overflow-y-auto bg-black/10 p-4">
                            <BodyView
                                body={tab === "request" ? requestBody : responseBody}
                                side={tab}
                            />
                        </div>
                    </div>
                </div>
            ) : null}
        </Modal>
    );
}
