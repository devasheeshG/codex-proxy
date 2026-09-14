"use client";

// ---------------------------------------------------------------------------
// Overview / analytics. A date range drives the whole page:
//   - Hero band: API-equivalent value delivered + tokens/requests, with the
//     activity series (auto hourly/daily) pulled in alongside.
//   - Infra strip: account / user / key inventory (not range-dependent).
//   - Two-up: peak hours + top users.
//   - Recent requests: paginated log for the range.
// Auto-refresh and the range selection both re-fetch every panel.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Cpu } from "lucide-react";
import { api } from "@/lib/api";
import {
    ActivityResponse,
    ByUserResponse,
    DistributionResponse,
    HourlyResponse,
    ModelMixResponse,
    OverviewStats,
    PercentileBreakdown,
    ThinkingLevelMixResponse,
    TimeRange,
    UsagePage,
    UsageRecord,
    UserLookup,
} from "@/lib/types";
import {
    formatCostUsd,
    formatDateTime,
    formatNumber,
    formatTokens,
    localTzAbbr,
} from "@/lib/format";
import {
    Badge,
    Button,
    Card,
    EmptyState,
    ErrorState,
    LoadingState,
    StatusToggle,
    TextInput,
    UsageBar,
} from "@/components/ui";
import {
    DualAreaChart,
    DualSeries,
    RankedBars,
    STACK_COLORS,
    StackedBarChart,
    StackSeries,
} from "@/components/Charts";
import { RangePicker } from "@/components/RangePicker";
import { UserFilter } from "@/components/UserFilter";
import { EventTypeFilter } from "@/components/EventTypeFilter";

const PAGE_SIZE = 25;
const AUTO_REFRESH_KEY = "dashboard_auto_refresh";
const REFRESH_SECS_KEY = "dashboard_refresh_secs";

// USD with no cents — the hero figure reads as a headline, not an invoice.
function formatUsd0(value: number): string {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
    }).format(value);
}

// Default window: the current local calendar month, applied explicitly so every panel shares it.
function thisMonth(): TimeRange {
    const end = new Date();
    const start = new Date(end.getFullYear(), end.getMonth(), 1);
    return { start: start.toISOString(), end: end.toISOString(), label: "This month" };
}

export default function OverviewPage() {
    const [range, setRange] = useState<TimeRange>(thisMonth);

    const [stats, setStats] = useState<OverviewStats | null>(null);
    const [activity, setActivity] = useState<ActivityResponse | null>(null);
    const [hourly, setHourly] = useState<HourlyResponse | null>(null);
    const [byUser, setByUser] = useState<ByUserResponse | null>(null);
    const [distro, setDistro] = useState<DistributionResponse | null>(null);
    const [modelMix, setModelMix] = useState<ModelMixResponse | null>(null);
    const [thinkingLevelMix, setThinkingLevelMix] = useState<ThinkingLevelMixResponse | null>(null);
    const [analyticsError, setAnalyticsError] = useState<string | null>(null);
    const [analyticsLoading, setAnalyticsLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const analyticsRequestId = useRef(0);

    // --- user filter ------------------------------------------------------------
    const [allUsers, setAllUsers] = useState<UserLookup[]>([]);
    const [filterUserId, setFilterUserId] = useState<string | null>(null);
    const [modelOptions, setModelOptions] = useState<string[]>([]);
    const [filterModel, setFilterModel] = useState("");

    useEffect(() => {
        api.analyticsUsers()
            .then(setAllUsers)
            .catch(() => {});
        api.analyticsModels()
            .then(setModelOptions)
            .catch(() => {});
    }, []);

    const loadAnalytics = useCallback(async (r: TimeRange, uid?: string | null, model?: string) => {
        const requestId = ++analyticsRequestId.current;
        setAnalyticsLoading(true);
        setAnalyticsError(null);
        setDistro(null);
        setModelMix(null);
        setThinkingLevelMix(null);
        const u = uid ?? undefined;
        try {
            const [s, a, h, bu] = await Promise.all([
                api.overview(r, u, model),
                api.activity(r, u, model),
                api.hourly(r, u, model),
                api.byUser(r, u, model),
            ]);
            if (requestId !== analyticsRequestId.current) return;
            setStats(s);
            setActivity(a);
            setHourly(h);
            setByUser(bu);
            const [d, m, t] = await Promise.all([
                api.distributions(r, u, model).catch(() => null),
                api.modelMix(r, u, model).catch(() => null),
                api.thinkingLevelMix(r, u, model).catch(() => null),
            ]);
            if (requestId !== analyticsRequestId.current) return;
            if (d) setDistro(d);
            if (m) setModelMix(m);
            if (t) setThinkingLevelMix(t);
        } catch (err) {
            if (requestId === analyticsRequestId.current) {
                setAnalyticsError(err instanceof Error ? err.message : "Failed to load analytics.");
            }
        } finally {
            if (requestId === analyticsRequestId.current) setAnalyticsLoading(false);
        }
    }, []);

    // --- usage table ---------------------------------------------------------
    const [offset, setOffset] = useState(0);
    const [usage, setUsage] = useState<UsagePage | null>(null);
    const [usageError, setUsageError] = useState<string | null>(null);
    const [usageLoading, setUsageLoading] = useState(true);
    const usageRequestId = useRef(0);

    const loadUsage = useCallback(
        async (off: number, r: TimeRange, uid?: string | null, model?: string) => {
            const requestId = ++usageRequestId.current;
            setUsageLoading(true);
            setUsageError(null);
            try {
                const nextUsage = await api.usage(PAGE_SIZE, off, r, uid ?? undefined, model);
                if (requestId === usageRequestId.current) setUsage(nextUsage);
            } catch (err) {
                if (requestId === usageRequestId.current) {
                    setUsageError(err instanceof Error ? err.message : "Failed to load usage.");
                }
            } finally {
                if (requestId === usageRequestId.current) setUsageLoading(false);
            }
        },
        [],
    );

    const refreshAll = useCallback(async () => {
        setRefreshing(true);
        try {
            await Promise.all([
                loadAnalytics(range, filterUserId, filterModel || undefined),
                loadUsage(0, range, filterUserId, filterModel || undefined),
            ]);
            setOffset(0);
        } finally {
            setRefreshing(false);
        }
    }, [filterModel, filterUserId, loadAnalytics, loadUsage, range]);

    // Reset to the first page and reload everything when the range or user filter changes.
    useEffect(() => {
        setOffset(0);
        void loadAnalytics(range, filterUserId, filterModel || undefined);
    }, [range, filterModel, filterUserId, loadAnalytics]);

    useEffect(() => {
        void loadUsage(offset, range, filterUserId, filterModel || undefined);
    }, [offset, range, filterModel, filterUserId, loadUsage]);

    // --- auto-refresh --------------------------------------------------------
    const [autoRefresh, setAutoRefresh] = useState(false);
    const [refreshSecs, setRefreshSecs] = useState(10);

    useEffect(() => {
        try {
            if (window.localStorage.getItem(AUTO_REFRESH_KEY) === "1") setAutoRefresh(true);
            const secs = Number(window.localStorage.getItem(REFRESH_SECS_KEY));
            if (Number.isFinite(secs) && secs > 0) setRefreshSecs(Math.floor(secs));
        } catch {
            // localStorage may be unavailable — fall back to defaults.
        }
    }, []);

    useEffect(() => {
        try {
            window.localStorage.setItem(AUTO_REFRESH_KEY, autoRefresh ? "1" : "0");
        } catch {
            /* ignore */
        }
    }, [autoRefresh]);

    useEffect(() => {
        try {
            window.localStorage.setItem(REFRESH_SECS_KEY, String(refreshSecs));
        } catch {
            /* ignore */
        }
    }, [refreshSecs]);

    useEffect(() => {
        if (!autoRefresh) return;
        const ms = Math.max(1, refreshSecs) * 1000;
        const id = setInterval(() => {
            void refreshAll();
        }, ms);
        return () => clearInterval(id);
    }, [autoRefresh, refreshSecs, refreshAll]);

    const total = usage?.total ?? 0;
    const showingFrom = total === 0 ? 0 : offset + 1;
    const showingTo = Math.min(offset + PAGE_SIZE, total);
    const canPrev = offset > 0;
    const canNext = offset + PAGE_SIZE < total;

    // Activity series, labelled by the granularity the backend chose.
    const granularity = activity?.granularity ?? "day";
    const activityData = useMemo(
        () =>
            (activity?.points ?? []).map((p) => {
                const d = new Date(p.ts);
                const axis =
                    granularity === "hour"
                        ? `${String(d.getHours()).padStart(2, "0")}:00`
                        : `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
                const label =
                    granularity === "hour"
                        ? new Intl.DateTimeFormat("en-US", {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                              hour12: true,
                          }).format(d)
                        : new Intl.DateTimeFormat("en-US", {
                              month: "short",
                              day: "numeric",
                          }).format(d);
                return { label, axis, requests: p.requests, tokens: p.tokens };
            }),
        [activity, granularity],
    );
    const activityDualSeries: [DualSeries, DualSeries] = [
        {
            key: "requests",
            label: "Requests",
            tone: "green",
            format: (v) => `${formatNumber(v)} req`,
        },
        { key: "tokens", label: "Tokens", tone: "blue", format: (v) => `${formatTokens(v)}` },
    ];
    const hourlyUsers = hourly?.users ?? [];
    const hourlyUserTotals = new Map<string, number>();
    for (const point of hourly?.points ?? []) {
        for (const slice of point.by_user) {
            hourlyUserTotals.set(
                slice.user_id,
                (hourlyUserTotals.get(slice.user_id) ?? 0) + slice.requests,
            );
        }
    }
    const topHourlyUsers = [...hourlyUsers]
        .sort(
            (a, b) =>
                (hourlyUserTotals.get(b.user_id) ?? 0) - (hourlyUserTotals.get(a.user_id) ?? 0),
        )
        .slice(0, 5);
    const topHourlyUserIds = new Set(topHourlyUsers.map((user) => user.user_id));
    const hasOtherHourlyUsers = hourlyUsers.length > topHourlyUsers.length;
    const hourlyStackSeries: StackSeries[] = topHourlyUsers.map((u, i) => ({
        key: u.user_id,
        label: u.user_name,
        color: STACK_COLORS[i % STACK_COLORS.length],
    }));
    if (hasOtherHourlyUsers) {
        hourlyStackSeries.push({
            key: "other-users",
            label: "Other users",
            color: "var(--color-fog-500)",
        });
    }
    const tz = localTzAbbr();
    const hourlyStackData = (hourly?.points ?? [])
        .map((h) => {
            const ref = new Date();
            ref.setUTCHours(h.hour, 0, 0, 0);
            const lh = ref.getHours();
            const lm = ref.getMinutes();
            const timeStr = `${String(lh).padStart(2, "0")}:${String(lm).padStart(2, "0")}`;
            const row: Record<string, string | number> & { label: string; axis: string } = {
                label: `${timeStr} ${tz}`,
                axis: lm === 0 ? String(lh) : timeStr,
            };
            for (const slice of h.by_user) {
                if (topHourlyUserIds.has(slice.user_id)) {
                    row[slice.user_id] = slice.requests;
                } else if (hasOtherHourlyUsers) {
                    row["other-users"] = Number(row["other-users"] ?? 0) + slice.requests;
                }
            }
            return { row, sortKey: lh * 60 + lm };
        })
        .sort((a, b) => a.sortKey - b.sortKey)
        .map(({ row }) => row);

    const topUsers = (byUser?.users ?? [])
        .filter((u) => u.tokens > 0)
        .slice(0, 6)
        .map((u) => ({
            label: u.user_name ?? "—",
            value: u.tokens,
            sublabel: `${formatNumber(u.requests)} req`,
        }));

    return (
        <div className="space-y-5">
            {/* Header */}
            <header className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h1 className="text-fog-100 font-serif text-2xl font-semibold tracking-tight">
                        Overview
                    </h1>
                    <p className="text-fog-400 mt-0.5 text-sm">
                        What your subscription pool delivered, and how it&apos;s being used.
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
                            onClick={() => setAutoRefresh((v) => !v)}
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
                                onChange={(e) => {
                                    const n = Number(e.target.value);
                                    if (Number.isFinite(n) && n > 0) setRefreshSecs(Math.floor(n));
                                }}
                                className="px-2 py-0.5 text-center disabled:opacity-50"
                            />
                        </div>
                        <span className="text-fog-400 text-xs">sec</span>
                    </div>
                    <Button
                        variant="ghost"
                        className="h-9"
                        onClick={() => void refreshAll()}
                        disabled={refreshing}
                    >
                        {refreshing ? "Refreshing…" : "Refresh"}
                    </Button>
                    <RangePicker value={range} onChange={setRange} />
                    <UserFilter users={allUsers} value={filterUserId} onChange={setFilterUserId} />
                    <EventTypeFilter
                        value={filterModel}
                        options={modelOptions}
                        onChange={(next) => {
                            setFilterModel(next);
                            setOffset(0);
                        }}
                        label="Model"
                        allLabel="All models"
                        idPrefix="overview-model-options"
                        compact
                        icon={Cpu}
                    />
                </div>
            </header>

            {analyticsLoading && !stats ? (
                <Card>
                    <LoadingState />
                </Card>
            ) : analyticsError && !stats ? (
                <Card>
                    <ErrorState
                        message={analyticsError}
                        onRetry={() =>
                            void loadAnalytics(range, filterUserId, filterModel || undefined)
                        }
                    />
                </Card>
            ) : stats ? (
                <>
                    {/* Hero: value + activity */}
                    <Card className="grid grid-cols-1 overflow-hidden lg:grid-cols-[minmax(260px,0.82fr)_1.18fr]">
                        <div className="flex flex-col p-6 sm:p-7">
                            <div className="text-fog-400 text-xs font-medium tracking-wider uppercase">
                                {range.label}
                            </div>
                            <div
                                className="text-fog-100 mt-3.5 font-serif text-6xl leading-none font-semibold tracking-tight"
                                style={{ fontVariantNumeric: "oldstyle-nums tabular-nums" }}
                            >
                                {formatUsd0(stats.api_equivalent_cost_usd)}
                            </div>
                            <div className="text-fog-300 mt-3.5 text-sm font-medium">
                                API-equivalent value delivered
                            </div>
                            <div className="mt-4 flex gap-6">
                                <div>
                                    <div className="text-fog-100 font-mono text-lg tabular-nums">
                                        {formatTokens(stats.tokens)}
                                    </div>
                                    <div className="text-fog-400 mt-1 text-[10px] tracking-wider uppercase">
                                        Tokens
                                    </div>
                                </div>
                                <div>
                                    <div className="text-fog-100 font-mono text-lg tabular-nums">
                                        {formatNumber(stats.requests)}
                                    </div>
                                    <div className="text-fog-400 mt-1 text-[10px] tracking-wider uppercase">
                                        Requests
                                    </div>
                                </div>
                            </div>
                            <p className="border-brand-500/55 text-fog-400 mt-auto border-l-2 pt-4 pl-3 font-serif text-xs italic">
                                Flat-rate subscriptions, measured at list API prices — value
                                delivered, not money owed.
                            </p>
                        </div>
                        <div className="border-ink-700 border-t p-5 lg:border-t-0 lg:border-l">
                            <div className="mb-2 flex items-center justify-between gap-2">
                                <span className="text-fog-400 text-xs">
                                    Activity per {granularity}
                                </span>
                                <div className="flex gap-3">
                                    {activityDualSeries.map((s) => (
                                        <span
                                            key={s.key}
                                            className="text-fog-300 flex items-center gap-1.5 text-xs"
                                        >
                                            <span
                                                className="h-2 w-2 shrink-0 rounded-full"
                                                style={{
                                                    backgroundColor: `var(--color-chart-${s.tone === "green" ? 1 : 2})`,
                                                }}
                                            />
                                            {s.label}
                                        </span>
                                    ))}
                                </div>
                            </div>
                            {activity ? (
                                <DualAreaChart
                                    data={activityData}
                                    series={activityDualSeries}
                                    height={210}
                                />
                            ) : (
                                <LoadingState />
                            )}
                        </div>
                    </Card>

                    <SystemTokenFlow stats={stats} rangeLabel={range.label} />

                    {/* Infra strip */}
                    <Card className="grid grid-cols-1 overflow-hidden md:grid-cols-4">
                        <PoolCapacityCell stats={stats} />
                        <StripCell
                            label="Accounts"
                            value={formatNumber(stats.active_accounts)}
                            sub={`/ ${formatNumber(stats.total_accounts)}`}
                            meta="active in rotation"
                            divided
                        />
                        <StripCell
                            label="Users"
                            value={formatNumber(stats.active_users)}
                            sub={`/ ${formatNumber(stats.total_users)}`}
                            meta="active key-holders"
                            divided
                        />
                        <StripCell
                            label="API keys"
                            value={formatNumber(stats.total_keys)}
                            meta="across all users"
                            divided
                        />
                    </Card>

                    {modelMix && modelMix.users.some((user) => user.total_requests > 0) ? (
                        <UserInsightsCard modelData={modelMix} thinkingData={thinkingLevelMix} />
                    ) : null}

                    {/* Independent columns avoid stretching short cards to match data-heavy neighbours. */}
                    {stats.requests > 0 ? (
                        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
                            <div className="space-y-4">
                                {modelMix &&
                                modelMix.users.some((user) => user.total_requests > 0) ? (
                                    <RequestsByModelCard data={modelMix} />
                                ) : null}
                                <PeakHoursCard
                                    hourly={hourly}
                                    data={hourlyStackData}
                                    series={hourlyStackSeries}
                                    timezone={tz}
                                />
                            </div>
                            <div className="space-y-4">
                                {distro && distro.total_requests > 0 ? (
                                    <DistributionCard distro={distro} />
                                ) : null}
                                <TopUsersCard data={topUsers} tone="green" />
                            </div>
                        </div>
                    ) : null}
                </>
            ) : null}

            {/* Request-level usage moved to the Events page; keep this legacy block disabled during rollout. */}
            {false ? (
                <section className="space-y-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-fog-100 text-sm font-semibold">Recent requests</h2>
                        {total > 0 ? (
                            <span className="text-fog-400 font-mono text-xs">
                                {formatNumber(showingFrom)}–{formatNumber(showingTo)} of{" "}
                                {formatNumber(total)}
                            </span>
                        ) : null}
                    </div>

                    <Card className="overflow-hidden">
                        {usageLoading ? (
                            <LoadingState />
                        ) : usageError ? (
                            <ErrorState
                                message={usageError ?? "Unable to load request history."}
                                onRetry={() =>
                                    void loadUsage(
                                        offset,
                                        range,
                                        filterUserId,
                                        filterModel || undefined,
                                    )
                                }
                            />
                        ) : usage && usage!.items.length > 0 ? (
                            <div className="max-h-[520px] overflow-auto overscroll-contain">
                                <table className="w-full text-sm">
                                    <thead className="bg-ink-850 sticky top-0 z-10">
                                        <tr className="border-ink-700 text-fog-400 border-b text-left text-xs tracking-wider uppercase">
                                            <th className="px-4 py-3 font-medium">Time</th>
                                            <th className="px-4 py-3 font-medium">Model</th>
                                            <th className="px-4 py-3 font-medium">Thinking</th>
                                            <th className="px-4 py-3 font-medium">Mode</th>
                                            <th className="px-4 py-3 font-medium">User</th>
                                            <th className="hidden px-4 py-3 font-medium lg:table-cell">
                                                Key
                                            </th>
                                            <th className="hidden px-4 py-3 font-medium lg:table-cell">
                                                Account
                                            </th>
                                            <th className="px-4 py-3 text-right font-medium">In</th>
                                            <th className="px-4 py-3 text-right font-medium">
                                                Out
                                            </th>
                                            <th
                                                className="px-4 py-3 text-right font-medium"
                                                title="Prompt tokens read from cache"
                                            >
                                                Cache read
                                            </th>
                                            <th
                                                className="px-4 py-3 text-right font-medium"
                                                title="Prompt tokens written to cache when reported by Codex"
                                            >
                                                Cache write
                                            </th>
                                            <th className="px-4 py-3 text-right font-medium">$</th>
                                            <th className="px-4 py-3 text-right font-medium">
                                                Status
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {usage!.items.map((r) => (
                                            <tr
                                                key={r.id}
                                                className="border-ink-800 hover:bg-ink-800/50 border-b last:border-0"
                                            >
                                                <td className="text-fog-300 px-4 py-2.5 font-mono whitespace-nowrap">
                                                    {formatDateTime(r.created_at)}
                                                </td>
                                                <td className="px-4 py-2.5 whitespace-nowrap">
                                                    <ModelCell record={r} />
                                                </td>
                                                <td className="text-fog-300 px-4 py-2.5 text-xs capitalize">
                                                    {r.reasoning_level ?? "—"}
                                                </td>
                                                <td className="text-fog-300 px-4 py-2.5 text-xs capitalize">
                                                    {r.request_mode}
                                                </td>
                                                <td className="text-fog-300 px-4 py-2.5 text-xs">
                                                    {r.user_name ?? "—"}
                                                </td>
                                                <td className="text-fog-400 hidden px-4 py-2.5 text-xs lg:table-cell">
                                                    {r.api_key_label ?? "—"}
                                                </td>
                                                <td className="text-fog-400 hidden px-4 py-2.5 text-xs lg:table-cell">
                                                    {r.fallback_provider_label
                                                        ? `${r.fallback_provider_label} · API fallback`
                                                        : (r.account_label ?? "—")}
                                                </td>
                                                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                                                    <TokenCell value={r.input_tokens} />
                                                </td>
                                                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                                                    <TokenCell value={r.output_tokens} />
                                                </td>
                                                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                                                    <TokenCell
                                                        value={r.cached_input_tokens}
                                                        showZero
                                                    />
                                                </td>
                                                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                                                    <TokenCell
                                                        value={r.cache_write_tokens}
                                                        showZero
                                                    />
                                                </td>
                                                <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                                                    <CostCell value={r.cost_usd} />
                                                </td>
                                                <td className="px-4 py-2.5 text-right">
                                                    <StatusBadge code={r.status_code} />
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <EmptyState message="No requests in this range." />
                        )}
                    </Card>

                    {total > 0
                        ? (() => {
                              const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
                              const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
                              return (
                                  <div className="flex items-center justify-end gap-3">
                                      <Button
                                          variant="ghost"
                                          disabled={!canPrev || usageLoading}
                                          onClick={() =>
                                              setOffset((o) => Math.max(0, o - PAGE_SIZE))
                                          }
                                          className="px-2"
                                      >
                                          ←
                                      </Button>
                                      <span className="text-fog-300 flex items-center gap-2 text-xs">
                                          <span>Page</span>
                                          <input
                                              type="number"
                                              min={1}
                                              max={totalPages}
                                              value={currentPage}
                                              onChange={(e) => {
                                                  const p = Math.max(
                                                      1,
                                                      Math.min(
                                                          totalPages,
                                                          Number(e.target.value) || 1,
                                                      ),
                                                  );
                                                  setOffset((p - 1) * PAGE_SIZE);
                                              }}
                                              className="border-ink-600 bg-ink-800 text-fog-100 w-12 rounded border px-1.5 py-0.5 text-center font-mono text-xs tabular-nums"
                                          />
                                          <span>of {totalPages}</span>
                                      </span>
                                      <Button
                                          variant="ghost"
                                          disabled={!canNext || usageLoading}
                                          onClick={() => setOffset((o) => o + PAGE_SIZE)}
                                          className="px-2"
                                      >
                                          →
                                      </Button>
                                  </div>
                              );
                          })()
                        : null}
                </section>
            ) : null}
        </div>
    );
}

function SystemTokenFlow({ stats, rangeLabel }: { stats: OverviewStats; rangeLabel: string }) {
    const inputRate = Math.max(0, Math.min(100, stats.input_rate_pct));
    const outputRate = Math.max(0, Math.min(100, stats.output_rate_pct));
    const cacheHitRate = Math.max(0, Math.min(100, stats.cache_hit_rate_pct));

    return (
        <Card className="grid grid-cols-1 overflow-hidden md:grid-cols-2">
            <div className="p-5 sm:p-6">
                <div className="flex items-center justify-between gap-3">
                    <div className="text-fog-400 text-[11px] font-medium tracking-wider uppercase">
                        Input vs output rate
                    </div>
                    <span className="text-fog-500 text-[10px] tracking-wide uppercase">
                        {rangeLabel}
                    </span>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-4">
                    <div>
                        <div className="text-fog-100 font-mono text-4xl font-semibold tabular-nums">
                            {inputRate.toFixed(2)}%
                        </div>
                        <div className="text-fog-400 mt-1 text-xs">of traffic is input</div>
                    </div>
                    <div>
                        <div className="text-fog-100 font-mono text-4xl font-semibold tabular-nums">
                            {outputRate.toFixed(2)}%
                        </div>
                        <div className="text-fog-400 mt-1 text-xs">of traffic is output</div>
                    </div>
                </div>
                <div
                    className="bg-ink-700 mt-5 flex h-2 overflow-hidden rounded-full"
                    role="img"
                    aria-label={`${formatTokens(stats.input_tokens)} input tokens and ${formatTokens(stats.output_tokens)} output tokens`}
                >
                    <span
                        className="h-full bg-[var(--color-chart-1)] transition-[width] duration-500"
                        style={{ width: `${inputRate}%` }}
                    />
                    <span
                        className="h-full bg-[var(--color-chart-2)] transition-[width] duration-500"
                        style={{ width: `${outputRate}%` }}
                    />
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3 font-mono text-xs tabular-nums">
                    <span className="text-fog-300">
                        <i className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-chart-1)]" />
                        {formatTokens(stats.input_tokens)} input
                    </span>
                    <span className="text-fog-300 text-right">
                        <i className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-chart-2)]" />
                        {formatTokens(stats.output_tokens)} output
                    </span>
                </div>
            </div>

            <div className="border-ink-700 border-t p-5 sm:p-6 md:border-t-0 md:border-l">
                <div className="text-fog-400 text-[11px] font-medium tracking-wider uppercase">
                    Input cache hit rate
                </div>
                <div className="mt-4 flex items-baseline justify-between gap-4">
                    <div className="text-fog-100 font-mono text-4xl font-semibold tabular-nums">
                        {cacheHitRate.toFixed(1)}%
                    </div>
                    <div className="text-fog-400 text-right text-xs">
                        {formatTokens(stats.cached_input_tokens)} cached of{" "}
                        {formatTokens(stats.input_tokens)} input tokens
                    </div>
                </div>
                <div
                    className="bg-ink-700 mt-5 h-2 overflow-hidden rounded-full"
                    role="progressbar"
                    aria-label="Input cache hit rate"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(cacheHitRate)}
                >
                    <div
                        className="bg-brand-500 h-full rounded-full transition-[width] duration-500"
                        style={{ width: `${cacheHitRate}%` }}
                    />
                </div>
            </div>
        </Card>
    );
}

// One cell of the account/user/key inventory strip.
function StripCell({
    label,
    value,
    sub,
    meta,
    divided,
}: {
    label: string;
    value: string;
    sub?: string;
    meta: string;
    divided?: boolean;
}) {
    return (
        <div
            className={`p-5 ${divided ? "border-ink-700 border-t md:border-t-0 md:border-l" : ""}`}
        >
            <div className="text-fog-400 text-[11px] font-medium tracking-wider uppercase">
                {label}
            </div>
            <div className="text-fog-100 mt-2 font-mono text-2xl tabular-nums">
                {value}
                {sub ? <span className="text-fog-400 text-base"> {sub}</span> : null}
            </div>
            <div className="text-fog-400 mt-1.5 text-xs">{meta}</div>
        </div>
    );
}

function PoolCapacityCell({ stats }: { stats: OverviewStats }) {
    const hasCapacity = stats.active_accounts > 0;
    const used = hasCapacity ? stats.pool_used_pct : 0;

    return (
        <div className="p-5">
            <div className="text-fog-400 text-[11px] font-medium tracking-wider uppercase">
                Proxy capacity
            </div>
            <div className="mt-2.5">
                {hasCapacity ? (
                    <UsageBar label="Used" fraction={used} />
                ) : (
                    <div
                        className="bg-ink-700 h-1.5 w-full overflow-hidden rounded-full"
                        role="progressbar"
                        aria-label="Active proxy capacity used"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={0}
                    />
                )}
            </div>
            <div className="text-fog-400 mt-2 text-xs">
                {hasCapacity
                    ? `Across ${formatNumber(stats.active_accounts)} active ${stats.active_accounts === 1 ? "account" : "accounts"}`
                    : "No active accounts in the pool"}
            </div>
        </div>
    );
}

function PeakHoursCard({
    hourly,
    data,
    series,
    timezone,
}: {
    hourly: HourlyResponse | null;
    data: Array<Record<string, string | number> & { label: string; axis: string }>;
    series: StackSeries[];
    timezone: string;
}) {
    return (
        <Card className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="text-fog-100 text-sm font-semibold">Peak hours</h3>
                    <p className="text-fog-400 mt-0.5 text-xs">
                        Requests by hour ({timezone}); five busiest users plus everyone else
                    </p>
                </div>
                {series.length > 0 ? (
                    <span className="border-ink-700 text-fog-400 rounded-md border px-2 py-1 text-[10px] tracking-wide uppercase">
                        {series.length} series
                    </span>
                ) : null}
            </div>
            <div className="mt-3">
                {hourly ? (
                    series.length > 0 ? (
                        <StackedBarChart
                            data={data}
                            series={series}
                            axisInterval={2}
                            format={(value) => `${formatNumber(value)} req`}
                            height={200}
                        />
                    ) : (
                        <div className="text-fog-400 flex h-44 items-center justify-center text-sm">
                            No usage in this range.
                        </div>
                    )
                ) : (
                    <LoadingState />
                )}
            </div>
            {series.length > 0 ? (
                <div className="border-ink-700 mt-3 flex max-h-16 flex-wrap gap-x-3 gap-y-1.5 overflow-y-auto border-t pt-3">
                    {series.map((item) => (
                        <span
                            key={item.key}
                            className="text-fog-300 flex items-center gap-1.5 text-xs"
                        >
                            <span
                                className="h-2.5 w-2.5 shrink-0 rounded-sm"
                                style={{ backgroundColor: item.color }}
                            />
                            {item.label}
                        </span>
                    ))}
                </div>
            ) : null}
        </Card>
    );
}

function TopUsersCard({
    data,
    tone,
}: {
    data: Array<{ label: string; value: number; sublabel: string }>;
    tone: "green";
}) {
    return (
        <Card className="p-5">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <h3 className="text-fog-100 text-sm font-semibold">Top users</h3>
                    <p className="text-fog-400 mt-0.5 text-xs">Six highest token consumers</p>
                </div>
                <span className="text-fog-500 text-[10px] tracking-wide uppercase">In range</span>
            </div>
            <div className="mt-4">
                {data.length > 0 ? (
                    <RankedBars data={data} tone={tone} format={(value) => formatTokens(value)} />
                ) : (
                    <div className="text-fog-400 flex h-44 items-center justify-center text-sm">
                        No usage in this range.
                    </div>
                )}
            </div>
        </Card>
    );
}

const MODEL_COLORS = [
    "var(--color-chart-1)",
    "var(--color-chart-2)",
    "var(--color-chart-3)",
    "var(--color-chart-4)",
    "#22c55e",
    "#38bdf8",
    "#818cf8",
    "#f59e0b",
];

function modelColor(index: number): string {
    return MODEL_COLORS[index % MODEL_COLORS.length];
}

function shortModel(model: string): string {
    return model.replace(/^codex-/, "").replace(/-20\d{6}$/, "");
}

type UserInsightView = "tokens" | "models" | "thinking";

function UserInsightsCard({
    modelData,
    thinkingData,
}: {
    modelData: ModelMixResponse;
    thinkingData: ThinkingLevelMixResponse | null;
}) {
    const [view, setView] = useState<UserInsightView>("tokens");
    const users = modelData.users.filter((user) => user.total_requests > 0);
    const allModels = Array.from(
        new Set(users.flatMap((user) => user.models.map((model) => model.model))),
    );
    const allLevels = sortThinkingLevels(
        Array.from(
            new Set(
                (thinkingData?.users ?? []).flatMap((user) =>
                    user.thinking_levels.map((item) => item.thinking_level),
                ),
            ),
        ),
    );
    const thinkingByUser = new Map((thinkingData?.users ?? []).map((user) => [user.user_id, user]));
    const totalRequests = users.reduce((sum, user) => sum + user.total_requests, 0);
    const thinkingTotals = new Map<string, number>();
    for (const user of thinkingData?.users ?? []) {
        for (const level of user.thinking_levels) {
            const key = thinkingLevelKey(level.thinking_level);
            thinkingTotals.set(key, (thinkingTotals.get(key) ?? 0) + level.requests);
        }
    }

    return (
        <Card className="overflow-hidden">
            <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <div className="text-fog-400 text-xs font-medium tracking-wider uppercase">
                        User analytics
                    </div>
                    <p className="text-fog-300 mt-1 text-sm">
                        Compare every active user without extending the page.
                    </p>
                    <div className="text-fog-500 mt-1 font-mono text-[11px]">
                        {formatNumber(users.length)} active users · {formatNumber(totalRequests)}{" "}
                        requests
                    </div>
                </div>
                <div
                    className="border-ink-700 bg-ink-900 grid grid-cols-3 rounded-lg border p-1"
                    role="tablist"
                    aria-label="User analytics view"
                >
                    {(["tokens", "models", "thinking"] as const).map((tab) => (
                        <button
                            key={tab}
                            type="button"
                            role="tab"
                            aria-selected={view === tab}
                            onClick={() => setView(tab)}
                            className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                                view === tab
                                    ? "bg-ink-700 text-fog-100"
                                    : "text-fog-400 hover:text-fog-200"
                            }`}
                        >
                            {tab}
                        </button>
                    ))}
                </div>
            </div>

            <UserInsightLegend
                view={view}
                models={allModels}
                levels={allLevels}
                thinkingTotals={thinkingTotals}
            />

            <div className="border-ink-700 max-h-[390px] overflow-y-auto overscroll-contain border-t">
                {users.length > 0 ? (
                    users.map((user) => {
                        const input = user.models.reduce(
                            (sum, model) => sum + model.input_tokens,
                            0,
                        );
                        const output = user.models.reduce(
                            (sum, model) => sum + model.output_tokens,
                            0,
                        );
                        const totalTokens = input + output;
                        const thinking = thinkingByUser.get(user.user_id);
                        return (
                            <div
                                key={user.user_id}
                                className="border-ink-800 grid gap-3 border-b px-5 py-3.5 last:border-0 sm:grid-cols-[minmax(130px,0.32fr)_1fr_110px] sm:items-center"
                            >
                                <div className="min-w-0">
                                    <div className="text-fog-100 truncate text-sm font-semibold">
                                        {user.user_name ?? "—"}
                                    </div>
                                    <div className="text-fog-500 mt-0.5 font-mono text-[10px] tabular-nums">
                                        {formatNumber(user.total_requests)} requests
                                    </div>
                                </div>

                                {view === "tokens" ? (
                                    <SplitTokenBar input={input} output={output} />
                                ) : view === "models" ? (
                                    <SegmentBar
                                        items={user.models.map((model) => ({
                                            key: model.model,
                                            label: shortModel(model.model),
                                            value: model.requests,
                                            color: modelColor(allModels.indexOf(model.model)),
                                        }))}
                                        total={user.total_requests}
                                    />
                                ) : thinking && thinking.total_requests > 0 ? (
                                    <SegmentBar
                                        items={thinking.thinking_levels.map((item) => ({
                                            key: thinkingLevelKey(item.thinking_level),
                                            label: thinkingLevelLabel(item.thinking_level),
                                            value: item.requests,
                                            color: modelColor(
                                                allLevels.indexOf(item.thinking_level),
                                            ),
                                        }))}
                                        total={thinking.total_requests}
                                    />
                                ) : (
                                    <div className="text-fog-500 text-xs">No thinking data</div>
                                )}

                                <div className="text-left sm:text-right">
                                    <div className="text-fog-100 font-mono text-sm tabular-nums">
                                        {view === "tokens"
                                            ? formatTokens(totalTokens)
                                            : formatNumber(user.total_requests)}
                                    </div>
                                    <div className="text-fog-500 mt-0.5 text-[10px] tracking-wide uppercase">
                                        {view === "tokens" ? "total tokens" : "requests"}
                                    </div>
                                </div>
                            </div>
                        );
                    })
                ) : (
                    <div className="text-fog-400 flex h-44 items-center justify-center text-sm">
                        No user activity in this range.
                    </div>
                )}
            </div>
            {users.length > 5 ? (
                <div className="border-ink-700 text-fog-500 border-t px-5 py-2 text-center text-[10px] tracking-wide uppercase">
                    Scroll inside this panel to inspect all {formatNumber(users.length)} users
                </div>
            ) : null}
        </Card>
    );
}

function UserInsightLegend({
    view,
    models,
    levels,
    thinkingTotals,
}: {
    view: UserInsightView;
    models: string[];
    levels: (string | null)[];
    thinkingTotals: Map<string, number>;
}) {
    const totalThinkingRequests = Array.from(thinkingTotals.values()).reduce(
        (sum, value) => sum + value,
        0,
    );
    const items: Array<{ key: string; label: string; color: string; meta?: string }> =
        view === "tokens"
            ? [
                  { key: "input", label: "Input tokens", color: "var(--color-chart-1)" },
                  { key: "output", label: "Output tokens", color: "var(--color-chart-2)" },
              ]
            : view === "models"
              ? models.map((model, index) => ({
                    key: model,
                    label: shortModel(model),
                    color: modelColor(index),
                }))
              : levels.map((level, index) => ({
                    key: thinkingLevelKey(level),
                    label: thinkingLevelLabel(level),
                    color: modelColor(index),
                    meta: `${formatNumber(thinkingTotals.get(thinkingLevelKey(level)) ?? 0)} · ${(totalThinkingRequests >
                    0
                        ? ((thinkingTotals.get(thinkingLevelKey(level)) ?? 0) /
                              totalThinkingRequests) *
                          100
                        : 0
                    ).toFixed(1)}%`,
                }));

    return (
        <div className="border-ink-700 bg-ink-900/45 flex max-h-20 flex-wrap gap-x-4 gap-y-1.5 overflow-y-auto border-t px-5 py-2.5">
            {items.map((item) => (
                <span key={item.key} className="text-fog-400 flex items-center gap-1.5 text-[11px]">
                    <span
                        className="h-2 w-2 shrink-0 rounded-sm"
                        style={{ backgroundColor: item.color }}
                    />
                    {item.label}
                    {item.meta ? (
                        <span className="text-fog-500 font-mono tabular-nums">{item.meta}</span>
                    ) : null}
                </span>
            ))}
        </div>
    );
}

function SplitTokenBar({ input, output }: { input: number; output: number }) {
    const total = input + output;
    const inputPct = total > 0 ? (input / total) * 100 : 0;
    const outputPct = total > 0 ? (output / total) * 100 : 0;
    return (
        <div>
            <div
                className="bg-ink-800 flex h-6 overflow-hidden rounded-md"
                title={`${formatTokens(input)} input · ${formatTokens(output)} output`}
            >
                <span
                    className="h-full bg-[var(--color-chart-1)]"
                    style={{ width: `${inputPct}%` }}
                />
                <span
                    className="h-full bg-[var(--color-chart-2)]"
                    style={{ width: `${outputPct}%` }}
                />
            </div>
            <div className="text-fog-500 mt-1 flex justify-between font-mono text-[10px]">
                <span>{formatTokens(input)} in</span>
                <span>{formatTokens(output)} out</span>
            </div>
        </div>
    );
}

function SegmentBar({
    items,
    total,
}: {
    items: Array<{ key: string; label: string; value: number; color: string }>;
    total: number;
}) {
    return (
        <div className="bg-ink-800 flex h-6 gap-px overflow-hidden rounded-md">
            {items.map((item) => {
                const pct = total > 0 ? Math.round((item.value / total) * 100) : 0;
                return (
                    <span
                        key={item.key}
                        className="flex h-full items-center justify-center text-[9px] font-semibold text-black/60"
                        style={{ flex: item.value, backgroundColor: item.color }}
                        title={`${item.label}: ${formatNumber(item.value)} requests (${pct}%)`}
                    >
                        {pct >= 12 ? `${pct}%` : ""}
                    </span>
                );
            })}
        </div>
    );
}

const THINKING_LEVEL_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh"];
function thinkingLevelKey(level: string | null): string {
    return level === null ? "unreported" : `reported:${level}`;
}

function thinkingLevelLabel(level: string | null): string {
    if (level === null) return "Not reported";
    return level.charAt(0).toUpperCase() + level.slice(1);
}

function sortThinkingLevels(levels: (string | null)[]): (string | null)[] {
    return [...levels].sort((a, b) => {
        if (a === null) return 1;
        if (b === null) return -1;
        const ai = THINKING_LEVEL_ORDER.indexOf(a);
        const bi = THINKING_LEVEL_ORDER.indexOf(b);
        if (ai === -1 && bi === -1) return a.localeCompare(b);
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
    });
}

function RequestsByModelCard({ data }: { data: ModelMixResponse }) {
    const byModel: Record<string, { requests: number }> = {};
    for (const u of data.users) {
        for (const m of u.models) {
            byModel[m.model] = byModel[m.model] || { requests: 0 };
            byModel[m.model].requests += m.requests;
        }
    }
    const sorted = Object.entries(byModel).sort((a, b) => b[1].requests - a[1].requests);
    const maxReq = sorted[0]?.[1].requests ?? 1;
    const totalReq = sorted.reduce((s, [, v]) => s + v.requests, 0);
    const allModels = Array.from(new Set(data.users.flatMap((u) => u.models.map((m) => m.model))));

    return (
        <Card className="p-5">
            <h3 className="text-fog-100 text-sm font-semibold">Requests by model</h3>
            <p className="text-fog-400 mt-0.5 mb-4 text-xs">Total requests per model in range</p>
            <div className="space-y-2.5">
                {sorted.map(([model, v]) => {
                    const pct = Math.round((v.requests / totalReq) * 100);
                    const ci = allModels.indexOf(model);
                    return (
                        <div key={model} className="flex items-center gap-2.5">
                            <span className="text-fog-200 w-28 shrink-0 truncate font-mono text-xs">
                                {shortModel(model)}
                            </span>
                            <div className="bg-ink-800 h-5 flex-1 overflow-hidden rounded">
                                <div
                                    className="h-full rounded"
                                    style={{
                                        width: `${Math.max((v.requests / maxReq) * 100, 2)}%`,
                                        backgroundColor: modelColor(ci),
                                        opacity: 0.85,
                                    }}
                                />
                            </div>
                            <span className="text-fog-200 w-16 text-right font-mono text-xs tabular-nums">
                                {formatNumber(v.requests)}
                            </span>
                            <span className="text-fog-400 w-8 text-right font-mono text-[11px] tabular-nums">
                                {pct}%
                            </span>
                        </div>
                    );
                })}
            </div>
        </Card>
    );
}

function CostCell({ value }: { value: number }) {
    if (!value || value <= 0) return <span className="text-fog-400">—</span>;
    return <span className="text-fog-400 text-xs">{formatCostUsd(value)}</span>;
}

function ModelCell({ record }: { record: UsageRecord }) {
    return <span className="text-fog-200 font-mono text-xs">{record.model}</span>;
}

// Zeros are common for cache misses; render them muted so the real numbers stand out.
function TokenCell({ value, showZero = false }: { value: number; showZero?: boolean }) {
    if (value <= 0) return <span className="text-fog-400">{showZero ? "0" : "·"}</span>;
    return <span className="text-fog-300">{formatTokens(value)}</span>;
}

function StatusBadge({ code }: { code: number | null }) {
    const tone =
        code == null
            ? "warn"
            : code >= 200 && code < 300
              ? "good"
              : code >= 400 && code < 500
                ? "warn"
                : "bad";
    return <Badge tone={tone}>{code ?? "—"}</Badge>;
}

const PERCENTILE_LABELS = ["Min", "P25", "P50", "P75", "P90", "P95", "Max"] as const;
const PERCENTILE_KEYS: (keyof PercentileBreakdown)[] = [
    "min",
    "p25",
    "p50",
    "p75",
    "p90",
    "p95",
    "max",
];

function DistributionCard({ distro }: { distro: DistributionResponse }) {
    function bars(breakdown: PercentileBreakdown, tone: "green" | "blue") {
        const color = tone === "green" ? "var(--color-chart-1)" : "var(--color-chart-2)";
        const localMax = Math.max(breakdown.max, 1);
        return (
            <div className="space-y-2">
                {PERCENTILE_KEYS.map((key, i) => {
                    const val = breakdown[key];
                    const pct = (val / localMax) * 100;
                    return (
                        <div key={key} className="flex items-center gap-3">
                            <span className="text-fog-400 w-8 text-right font-mono text-[11px]">
                                {PERCENTILE_LABELS[i]}
                            </span>
                            <div className="bg-ink-900 relative h-5 flex-1 overflow-hidden rounded">
                                <div
                                    className="h-full rounded transition-all duration-300"
                                    style={{
                                        width: `${Math.max(pct, 0.5)}%`,
                                        backgroundColor: color,
                                    }}
                                />
                            </div>
                            <span className="text-fog-200 w-20 text-right font-mono text-xs tabular-nums">
                                {formatTokens(val)}
                            </span>
                        </div>
                    );
                })}
            </div>
        );
    }

    return (
        <Card className="p-5">
            <div className="flex items-baseline justify-between">
                <div className="text-fog-400 text-xs font-medium tracking-wider uppercase">
                    Per-request token distribution
                </div>
                <span className="text-fog-400 font-mono text-xs">
                    {formatNumber(distro.total_requests)} requests in range
                </span>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div>
                    <h4 className="text-fog-200 mb-3 text-xs font-semibold tracking-wider uppercase">
                        Input tokens
                    </h4>
                    {bars(distro.input_tokens, "green")}
                </div>
                <div>
                    <h4 className="text-fog-200 mb-3 text-xs font-semibold tracking-wider uppercase">
                        Output tokens
                    </h4>
                    {bars(distro.output_tokens, "blue")}
                </div>
            </div>
        </Card>
    );
}
