"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ProxyEvent, TimeRange, UserLookup } from "@/lib/types";
import { RangePicker } from "@/components/RangePicker";
import { UserFilter } from "@/components/UserFilter";
import { EventTypeFilter } from "@/components/EventTypeFilter";
import { RequestCaptureOverlay } from "@/components/RequestCaptureOverlay";
import { Button, StatusToggle, TextInput } from "@/components/ui";
import { formatCompactNumber, formatDateTime } from "@/lib/format";

const EVENT_TYPES = [
    "request.received",
    "account.attempt",
    "account.capacity",
    "account.cooldown",
    "account.rate_limited",
    "account.selected",
    "fallback.attempt",
    "fallback.capacity",
    "response.returned",
    "request.exhausted",
];
const initialRange = (): TimeRange => {
    const now = new Date();
    return {
        start: new Date(now.getFullYear(), now.getMonth(), 1).toISOString(),
        end: now.toISOString(),
        label: "This month",
    };
};
const PAGE_SIZE = 50;
const AUTO_REFRESH_KEY = "dashboard_auto_refresh";
const REFRESH_SECS_KEY = "dashboard_refresh_secs";
const rowTone = (type: string) =>
    type.includes("exhausted") || type.includes("error") || type.includes("capacity")
        ? "bg-bad-500/10 hover:bg-bad-500/15"
        : type.includes("cooldown") || type.includes("rate_limited")
          ? "bg-warn-500/10 hover:bg-warn-500/15"
          : type.includes("selected") || type.includes("returned")
            ? "bg-good-500/10 hover:bg-good-500/15"
            : "hover:bg-brand-500/5";

function displayModel(metadata: Record<string, unknown>): string {
    const effective = String(metadata.model ?? "—");
    const requested = typeof metadata.requested_model === "string" ? metadata.requested_model : "";
    return requested && requested !== effective ? `${effective} (${requested})` : effective;
}

function CacheReadMetric({ cached, input }: { cached: unknown; input: unknown }) {
    if (cached == null || input == null) return <>—</>;
    const cachedTokens = Number(cached);
    const inputTokens = Number(input);
    if (!Number.isFinite(cachedTokens) || !Number.isFinite(inputTokens)) return <>—</>;
    if (inputTokens <= 0) return <>{formatCompactNumber(cachedTokens)}</>;

    const percentage = (cachedTokens / inputTokens) * 100;
    const tone =
        percentage > 90 ? "text-good-400" : percentage >= 50 ? "text-warn-400" : "text-bad-400";
    return (
        <span className="whitespace-nowrap">
            {formatCompactNumber(cachedTokens)}{" "}
            <span className={tone}>({percentage.toFixed(1)}%)</span>
        </span>
    );
}

export default function EventsPage() {
    const [range, setRange] = useState<TimeRange>(initialRange);
    const [users, setUsers] = useState<UserLookup[]>([]);
    const [models, setModels] = useState<string[]>([]);
    const [userId, setUserId] = useState<string | null>(null);
    const [eventType, setEventType] = useState("");
    const [model, setModel] = useState("");
    const [events, setEvents] = useState<ProxyEvent[]>([]);
    const [total, setTotal] = useState(0);
    const [offset, setOffset] = useState(0);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(false);
    const [refreshSecs, setRefreshSecs] = useState(10);
    const [error, setError] = useState<string | null>(null);
    const [captureEventId, setCaptureEventId] = useState<string | null>(null);
    const load = useCallback(
        (pageOffset = offset, selectedEventType = eventType, selectedModel = model) => {
            setLoading(true);
            setError(null);
            return api
                .events(
                    PAGE_SIZE,
                    pageOffset,
                    selectedEventType || undefined,
                    userId || undefined,
                    range.start,
                    range.end,
                    undefined,
                    selectedModel || undefined,
                )
                .then((page) => {
                    setEvents(page.events);
                    setTotal(page.total);
                })
                .catch((e) => setError(e instanceof Error ? e.message : "Unable to load events."))
                .finally(() => setLoading(false));
        },
        [eventType, model, offset, range.end, range.start, userId],
    );
    const refreshEvents = useCallback(async () => {
        setRefreshing(true);
        try {
            await load(0);
            setOffset(0);
        } finally {
            setRefreshing(false);
        }
    }, [load]);
    useEffect(() => {
        api.analyticsUsers()
            .then(setUsers)
            .catch(() => {});
        api.analyticsModels()
            .then(setModels)
            .catch(() => {});
    }, []);
    useEffect(() => {
        load(offset);
    }, [load, offset]);
    useEffect(() => {
        try {
            if (window.localStorage.getItem(AUTO_REFRESH_KEY) === "1") setAutoRefresh(true);
            const seconds = Number(window.localStorage.getItem(REFRESH_SECS_KEY));
            if (Number.isFinite(seconds) && seconds > 0) setRefreshSecs(Math.floor(seconds));
        } catch {}
    }, []);
    useEffect(() => {
        try {
            window.localStorage.setItem(AUTO_REFRESH_KEY, autoRefresh ? "1" : "0");
        } catch {}
    }, [autoRefresh]);
    useEffect(() => {
        try {
            window.localStorage.setItem(REFRESH_SECS_KEY, String(refreshSecs));
        } catch {}
    }, [refreshSecs]);
    useEffect(() => {
        if (!autoRefresh) return;
        const id = setInterval(() => void refreshEvents(), Math.max(1, refreshSecs) * 1000);
        return () => clearInterval(id);
    }, [autoRefresh, refreshEvents, refreshSecs]);
    return (
        <main className="space-y-5">
            <header className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h1 className="text-fog-100 font-serif text-2xl font-semibold tracking-tight">
                        Proxy events
                    </h1>
                    <p className="text-fog-400 mt-0.5 text-sm">
                        Trace request routing, retries, capacity failures, cooldowns, and completed
                        responses.
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                    {autoRefresh ? (
                        <span className="text-good-500 inline-flex items-center gap-1.5 text-xs font-medium tracking-wider uppercase">
                            <span className="bg-good-500 h-1.5 w-1.5 animate-pulse rounded-full" />
                            Live
                        </span>
                    ) : null}
                    <div className="border-ink-700 bg-ink-900 flex h-9 items-center gap-2 rounded-lg border px-3">
                        <StatusToggle
                            on={autoRefresh}
                            onClick={() => setAutoRefresh((current) => !current)}
                            onLabel="Auto"
                            offLabel="Auto"
                            srLabel={autoRefresh ? "Disable auto-refresh" : "Enable auto-refresh"}
                        />
                        <div className="w-14">
                            <TextInput
                                type="number"
                                min={1}
                                value={String(refreshSecs)}
                                disabled={!autoRefresh}
                                aria-label="Auto-refresh interval in seconds"
                                onChange={(event) => {
                                    const seconds = Number(event.target.value);
                                    if (Number.isFinite(seconds) && seconds > 0)
                                        setRefreshSecs(Math.floor(seconds));
                                }}
                                className="px-2 py-0.5 text-center disabled:opacity-50"
                            />
                        </div>
                        <span className="text-fog-400 text-xs">sec</span>
                    </div>
                    <Button
                        variant="ghost"
                        className="h-9"
                        onClick={() => void refreshEvents()}
                        disabled={refreshing}
                    >
                        {refreshing ? "Refreshing…" : "Refresh"}
                    </Button>
                    <RangePicker
                        value={range}
                        onChange={(next) => {
                            setRange(next);
                            setOffset(0);
                        }}
                    />
                    <UserFilter
                        users={users}
                        value={userId}
                        onChange={(next) => {
                            setUserId(next);
                            setOffset(0);
                        }}
                    />
                </div>
            </header>
            <div className="border-ink-700 bg-ink-900 flex flex-wrap items-end gap-3 rounded-xl border p-3">
                <EventTypeFilter value={eventType} options={EVENT_TYPES} onChange={setEventType} />
                <EventTypeFilter
                    value={model}
                    options={models}
                    onChange={setModel}
                    label="Model"
                    allLabel="All models"
                    idPrefix="event-model-options"
                />
                <button
                    onClick={() => {
                        setOffset(0);
                        load(0);
                    }}
                    className="bg-brand-500 text-ink-950 rounded-lg px-3 py-2 text-sm font-semibold"
                >
                    Apply filters
                </button>
            </div>
            <section className="border-ink-700 bg-ink-900 overflow-hidden rounded-xl border">
                <div className="border-ink-700 text-fog-400 flex justify-between border-b px-4 py-3 text-xs font-medium uppercase sm:px-5">
                    <span>Event log</span>
                    <span>{total} events</span>
                </div>
                {loading ? (
                    <div className="text-fog-400 p-6 text-sm">Loading…</div>
                ) : error ? (
                    <div className="text-bad-400 p-6 text-sm">{error}</div>
                ) : events.length === 0 ? (
                    <div className="text-fog-400 p-6 text-sm">No events in this range.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[1220px] text-left text-sm">
                            <thead className="bg-ink-850 text-fog-400 border-ink-700 border-b text-xs uppercase">
                                <tr>
                                    {[
                                        "Date / time",
                                        "User",
                                        "Model",
                                        "Thinking",
                                        "Account / provider",
                                        "Event",
                                        "Input",
                                        "Output",
                                        "Cache read",
                                        "Cache write",
                                        "Cost",
                                        "Status",
                                    ].map((x) => (
                                        <th key={x} className="px-3 py-3 font-medium">
                                            {x}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {events.map((e) => {
                                    const m = e.metadata as Record<string, unknown>;
                                    return (
                                        <tr
                                            key={e.id}
                                            className={`border-ink-800 border-b align-top ${rowTone(e.event_type)}`}
                                        >
                                            <td className="text-fog-400 px-3 py-3 font-mono text-xs whitespace-nowrap">
                                                {formatDateTime(e.created_at)}
                                            </td>
                                            <td className="text-fog-300 px-3 py-3 text-xs">
                                                {users.find((u) => u.id === e.user_id)?.name ?? "—"}
                                            </td>
                                            <td className="text-fog-300 px-3 py-3 text-xs">
                                                {displayModel(m)}
                                            </td>
                                            <td className="text-fog-300 px-3 py-3 text-xs">
                                                {String(
                                                    m.thinking_level ?? m.reasoning_level ?? "—",
                                                )}
                                            </td>
                                            <td className="text-fog-400 w-64 max-w-64 px-3 py-3 font-mono text-xs whitespace-normal">
                                                <span className="block [overflow-wrap:anywhere] break-words">
                                                    {String(m.account_name ?? "—")}
                                                </span>
                                            </td>
                                            <td className="text-brand-300 px-3 py-3 font-medium whitespace-nowrap">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span>{e.event_type}</span>
                                                    {(e.event_type === "request.received" ||
                                                        e.event_type === "response.returned") &&
                                                    e.archive_hot !== false &&
                                                    e.request_id?.startsWith("req_") ? (
                                                        <button
                                                            type="button"
                                                            onClick={() =>
                                                                setCaptureEventId(
                                                                    e.request_id.slice(4),
                                                                )
                                                            }
                                                            className="text-fog-300 hover:text-brand-200 rounded border border-current/30 px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase"
                                                        >
                                                            View capture
                                                        </button>
                                                    ) : null}
                                                </div>
                                            </td>
                                            <td className="px-3 py-3 font-mono text-xs">
                                                {formatCompactNumber(m.input_tokens)}
                                            </td>
                                            <td className="px-3 py-3 font-mono text-xs">
                                                {formatCompactNumber(m.output_tokens)}
                                            </td>
                                            <td className="px-3 py-3 font-mono text-xs">
                                                <CacheReadMetric
                                                    cached={m.cached_input_tokens}
                                                    input={m.input_tokens}
                                                />
                                            </td>
                                            <td className="px-3 py-3 font-mono text-xs">
                                                {formatCompactNumber(m.cache_write_tokens)}
                                            </td>
                                            <td className="px-3 py-3 font-mono text-xs">
                                                {m.cost_usd == null
                                                    ? "—"
                                                    : `$${Number(m.cost_usd).toFixed(4)}`}
                                            </td>
                                            <td className="text-fog-300 px-3 py-3">
                                                {e.status_code ?? "—"}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </section>
            {total > PAGE_SIZE ? (
                <div className="flex items-center justify-end gap-3">
                    <button
                        disabled={offset === 0 || loading}
                        onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                        className="border-ink-700 bg-ink-900 text-fog-200 rounded-lg border px-3 py-2 text-sm disabled:opacity-40"
                    >
                        Previous
                    </button>
                    <span className="text-fog-400 text-xs">
                        Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil(total / PAGE_SIZE)}
                    </span>
                    <button
                        disabled={offset + PAGE_SIZE >= total || loading}
                        onClick={() => setOffset(offset + PAGE_SIZE)}
                        className="border-ink-700 bg-ink-900 text-fog-200 rounded-lg border px-3 py-2 text-sm disabled:opacity-40"
                    >
                        Next
                    </button>
                </div>
            ) : null}
            {captureEventId ? (
                <RequestCaptureOverlay
                    eventId={captureEventId}
                    onClose={() => setCaptureEventId(null)}
                />
            ) : null}
        </main>
    );
}
