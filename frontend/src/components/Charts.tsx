"use client";

// ---------------------------------------------------------------------------
// Dashboard charts, built on Recharts and themed to the neutral Codex palette.
//
//   - TimeSeriesChart: area or bar over a time axis (daily volume, hourly peaks)
//     with hairline horizontal gridlines, muted axes and a dark tooltip card.
//   - RankedBars: a horizontal ranked list (e.g. top users) — clearer than a
//     vertical bar for a handful of labelled categories.
//   - ChartCard: the surrounding card with title, subtitle and an optional
//     total / control row.
//
// Colours come from the --color-chart-* tokens in globals.css.
// ---------------------------------------------------------------------------

import { ReactNode, useMemo, useState } from "react";
import { ActivityResponse } from "@/lib/types";
import {
    Area,
    AreaChart,
    Line,
    LineChart,
    Bar,
    BarChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { formatNumber } from "@/lib/format";

export type ChartTone = "green" | "blue" | "violet" | "amber";

const TONE_COLOR: Record<ChartTone, string> = {
    green: "var(--color-chart-1)",
    blue: "var(--color-chart-2)",
    violet: "var(--color-chart-3)",
    amber: "var(--color-chart-4)",
};

const GRID = "var(--color-chart-grid)";
const AXIS = "var(--color-chart-axis)";

export interface ChartDatum {
    label: string; // full label (shown in the tooltip), e.g. "2026-06-21"
    value: number;
    axis?: string; // short x-axis caption (defaults to label)
}

// Compact y-axis ticks: 950 -> "950", 12_300 -> "12.3k", 1_200_000 -> "1.2M".
function compact(value: number): string {
    if (Math.abs(value) < 1_000) return String(value);
    if (Math.abs(value) < 1_000_000) return `${trimDecimal(value / 1_000)}k`;
    if (Math.abs(value) < 1_000_000_000) return `${trimDecimal(value / 1_000_000)}M`;
    return `${trimDecimal(value / 1_000_000_000)}B`;
}
function trimDecimal(n: number): string {
    return n.toFixed(1).replace(/\.0$/, "");
}

// --- Tooltip ----------------------------------------------------------------

function ChartTooltip({
    active,
    payload,
    color,
    format,
}: {
    active?: boolean;
    payload?: Array<{ payload: ChartDatum }>;
    color: string;
    format: (v: number) => string;
}) {
    if (!active || !payload || payload.length === 0) return null;
    const datum = payload[0].payload;
    return (
        <div className="border-ink-600 bg-ink-900/95 rounded-md border px-3 py-2 shadow-[var(--shadow-pop)] backdrop-blur-sm">
            <div className="text-fog-400 text-[11px]">{datum.label}</div>
            <div className="mt-0.5 flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                <span className="text-fog-100 font-mono text-sm font-semibold tabular-nums">
                    {format(datum.value)}
                </span>
            </div>
        </div>
    );
}

// --- Time-series (area / bar) ----------------------------------------------

export function TimeSeriesChart({
    data,
    kind = "area",
    tone = "green",
    height = 220,
    format = (v: number) => formatNumber(v),
    axisInterval = "preserveStartEnd",
}: {
    data: ChartDatum[];
    kind?: "area" | "bar";
    tone?: ChartTone;
    height?: number;
    format?: (v: number) => string;
    axisInterval?: number | "preserveStartEnd";
}) {
    const color = TONE_COLOR[tone];
    const gradientId = `grad-${tone}`;

    const axisTick = { fontSize: 11, fill: AXIS };
    const commonAxes = (
        <>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
            <XAxis
                dataKey="axis"
                tickLine={false}
                axisLine={false}
                tick={axisTick}
                interval={axisInterval}
                minTickGap={12}
                dy={6}
            />
            <YAxis
                tickLine={false}
                axisLine={false}
                tick={axisTick}
                width={42}
                tickFormatter={compact}
                allowDecimals={false}
            />
            <Tooltip
                cursor={{ fill: "var(--color-fog-100)", fillOpacity: 0.04 }}
                content={<ChartTooltip color={color} format={format} />}
            />
        </>
    );

    return (
        <div style={{ height, width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
                {kind === "area" ? (
                    <AreaChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
                        <defs>
                            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={color} stopOpacity={0.32} />
                                <stop offset="100%" stopColor={color} stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        {commonAxes}
                        <Area
                            type="monotone"
                            dataKey="value"
                            stroke={color}
                            strokeWidth={2}
                            fill={`url(#${gradientId})`}
                            isAnimationActive={false}
                            activeDot={{ r: 3.5, strokeWidth: 0 }}
                            dot={false}
                        />
                    </AreaChart>
                ) : (
                    <BarChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
                        {commonAxes}
                        <Bar
                            dataKey="value"
                            fill={color}
                            fillOpacity={0.85}
                            radius={[3, 3, 0, 0]}
                            maxBarSize={34}
                            isAnimationActive={false}
                        />
                    </BarChart>
                )}
            </ResponsiveContainer>
        </div>
    );
}

// --- Dual-area chart (two overlaid series with independent formatting) -------

export interface DualSeries {
    key: string;
    label: string;
    tone: ChartTone;
    format: (v: number) => string;
}

function DualAreaTooltip({
    active,
    payload,
    series,
}: {
    active?: boolean;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    payload?: Array<{ payload: any }>;
    series: [DualSeries, DualSeries];
}) {
    if (!active || !payload || payload.length === 0) return null;
    const row = payload[0].payload;
    return (
        <div className="border-ink-600 bg-ink-900/95 rounded-md border px-3 py-2 shadow-[var(--shadow-pop)] backdrop-blur-sm">
            <div className="text-fog-400 text-[11px]">{row.label}</div>
            <div className="mt-1 space-y-0.5">
                {series.map((s) => (
                    <div key={s.key} className="flex items-center gap-1.5">
                        <span
                            className="h-2 w-2 shrink-0 rounded-full"
                            style={{ backgroundColor: TONE_COLOR[s.tone] }}
                        />
                        <span className="text-fog-300 text-[11px]">{s.label}</span>
                        <span className="text-fog-100 ml-auto font-mono text-[11px] font-semibold tabular-nums">
                            {s.format(Number(row[s.key]) || 0)}
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

export function DualAreaChart({
    data,
    series,
    height = 220,
    axisInterval = "preserveStartEnd",
}: {
    data: Array<Record<string, string | number> & { label: string; axis?: string }>;
    series: [DualSeries, DualSeries];
    height?: number;
    axisInterval?: number | "preserveStartEnd";
}) {
    const axisTick = { fontSize: 11, fill: AXIS };

    return (
        <div style={{ height, width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
                    <defs>
                        {series.map((s) => (
                            <linearGradient
                                key={s.key}
                                id={`grad-dual-${s.key}`}
                                x1="0"
                                y1="0"
                                x2="0"
                                y2="1"
                            >
                                <stop
                                    offset="0%"
                                    stopColor={TONE_COLOR[s.tone]}
                                    stopOpacity={0.25}
                                />
                                <stop
                                    offset="100%"
                                    stopColor={TONE_COLOR[s.tone]}
                                    stopOpacity={0}
                                />
                            </linearGradient>
                        ))}
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                    <XAxis
                        dataKey="axis"
                        tickLine={false}
                        axisLine={false}
                        tick={axisTick}
                        interval={axisInterval}
                        minTickGap={12}
                        dy={6}
                    />
                    <YAxis
                        yAxisId="left"
                        tickLine={false}
                        axisLine={false}
                        tick={axisTick}
                        width={42}
                        tickFormatter={compact}
                        allowDecimals={false}
                    />
                    <YAxis
                        yAxisId="right"
                        orientation="right"
                        tickLine={false}
                        axisLine={false}
                        tick={axisTick}
                        width={42}
                        tickFormatter={compact}
                        allowDecimals={false}
                    />
                    <Tooltip
                        cursor={{ fill: "var(--color-fog-100)", fillOpacity: 0.04 }}
                        content={<DualAreaTooltip series={series} />}
                    />
                    <Area
                        yAxisId="left"
                        type="monotone"
                        dataKey={series[0].key}
                        stroke={TONE_COLOR[series[0].tone]}
                        strokeWidth={2}
                        fill={`url(#grad-dual-${series[0].key})`}
                        isAnimationActive={false}
                        activeDot={{ r: 3.5, strokeWidth: 0 }}
                        dot={false}
                    />
                    <Area
                        yAxisId="right"
                        type="monotone"
                        dataKey={series[1].key}
                        stroke={TONE_COLOR[series[1].tone]}
                        strokeWidth={2}
                        fill={`url(#grad-dual-${series[1].key})`}
                        isAnimationActive={false}
                        activeDot={{ r: 3.5, strokeWidth: 0 }}
                        dot={false}
                    />
                </AreaChart>
            </ResponsiveContainer>
        </div>
    );
}

// --- Stacked bar chart (e.g. peak hours by user) ----------------------------

export const STACK_COLORS = [
    "var(--color-chart-1)",
    "var(--color-chart-2)",
    "var(--color-chart-3)",
    "var(--color-chart-4)",
    "#22c55e",
    "#38bdf8",
    "#818cf8",
    "#f59e0b",
];

export interface StackSeries {
    key: string;
    label: string;
    color: string;
}

function StackedTooltip({
    active,
    payload,
    series,
    format,
}: {
    active?: boolean;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    payload?: Array<{ payload: any }>;
    series: StackSeries[];
    format: (v: number) => string;
}) {
    if (!active || !payload || payload.length === 0) return null;
    const row = payload[0].payload;
    const total = series.reduce((s, sr) => s + (Number(row[sr.key]) || 0), 0);
    return (
        <div className="border-ink-600 bg-ink-900/95 rounded-md border px-3 py-2 shadow-[var(--shadow-pop)] backdrop-blur-sm">
            <div className="text-fog-400 text-[11px]">{row.label}</div>
            <div className="text-fog-100 mt-0.5 font-mono text-sm font-semibold tabular-nums">
                {format(total)}
            </div>
            <div className="mt-1.5 space-y-0.5">
                {series
                    .filter((s) => (Number(row[s.key]) || 0) > 0)
                    .map((s) => (
                        <div key={s.key} className="flex items-center gap-1.5">
                            <span
                                className="h-2 w-2 shrink-0 rounded-full"
                                style={{ backgroundColor: s.color }}
                            />
                            <span className="text-fog-300 text-[11px]">{s.label}</span>
                            <span className="text-fog-100 ml-auto font-mono text-[11px] tabular-nums">
                                {format(Number(row[s.key]) || 0)}
                            </span>
                        </div>
                    ))}
            </div>
        </div>
    );
}

export function StackedBarChart({
    data,
    series,
    height = 220,
    format = (v: number) => formatNumber(v),
    axisInterval = "preserveStartEnd",
}: {
    data: Array<Record<string, string | number> & { label: string; axis?: string }>;
    series: StackSeries[];
    height?: number;
    format?: (v: number) => string;
    axisInterval?: number | "preserveStartEnd";
}) {
    const axisTick = { fontSize: 11, fill: AXIS };

    return (
        <div style={{ height, width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                    <XAxis
                        dataKey="axis"
                        tickLine={false}
                        axisLine={false}
                        tick={axisTick}
                        interval={axisInterval}
                        minTickGap={12}
                        dy={6}
                    />
                    <YAxis
                        tickLine={false}
                        axisLine={false}
                        tick={axisTick}
                        width={42}
                        tickFormatter={compact}
                        allowDecimals={false}
                    />
                    <Tooltip
                        cursor={{ fill: "var(--color-fog-100)", fillOpacity: 0.04 }}
                        content={<StackedTooltip series={series} format={format} />}
                    />
                    {series.map((s, i) => (
                        <Bar
                            key={s.key}
                            dataKey={s.key}
                            stackId="a"
                            fill={s.color}
                            fillOpacity={0.85}
                            radius={i === series.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]}
                            maxBarSize={34}
                            isAnimationActive={false}
                        />
                    ))}
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}

// --- Ranked horizontal bars (e.g. top users) --------------------------------

export interface RankedDatum {
    label: string;
    value: number;
    sublabel?: string;
}

export function RankedBars({
    data,
    tone = "green",
    format = (v: number) => formatNumber(v),
}: {
    data: RankedDatum[];
    tone?: ChartTone;
    format?: (v: number) => string;
}) {
    const color = TONE_COLOR[tone];
    const max = Math.max(1, ...data.map((d) => d.value));

    return (
        <ul className="space-y-3">
            {data.map((d, i) => (
                <li key={i} className="group">
                    <div className="mb-1 flex items-baseline justify-between gap-3">
                        <span className="text-fog-200 flex min-w-0 items-center gap-2 text-sm">
                            <span className="text-fog-400 w-4 shrink-0 text-right font-mono text-xs tabular-nums">
                                {i + 1}
                            </span>
                            <span className="truncate">{d.label}</span>
                            {d.sublabel ? (
                                <span className="text-fog-400 shrink-0 text-[11px]">
                                    {d.sublabel}
                                </span>
                            ) : null}
                        </span>
                        <span className="text-fog-100 shrink-0 font-mono text-sm tabular-nums">
                            {format(d.value)}
                        </span>
                    </div>
                    <div className="bg-ink-800 h-5 w-full overflow-hidden rounded">
                        <div
                            className="h-full rounded transition-all"
                            style={{
                                width: `${Math.max((d.value / max) * 100, 2)}%`,
                                backgroundColor: color,
                                opacity: 0.85,
                            }}
                        />
                    </div>
                </li>
            ))}
        </ul>
    );
}

// --- Section wrapper --------------------------------------------------------

export function ChartCard({
    title,
    subtitle,
    total,
    right,
    children,
}: {
    title: string;
    subtitle?: string;
    total?: ReactNode;
    right?: ReactNode;
    children: ReactNode;
}) {
    return (
        <div className="border-ink-700 bg-ink-850 rounded-xl border p-5 shadow-[var(--shadow-card)]">
            <div className="mb-4 flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <h3 className="text-fog-100 text-sm font-semibold">{title}</h3>
                    </div>
                    {subtitle ? <p className="text-fog-400 mt-0.5 text-xs">{subtitle}</p> : null}
                </div>
                <div className="flex shrink-0 items-center gap-3">
                    {total ? (
                        <div className="text-fog-100 font-mono text-sm font-semibold tabular-nums">
                            {total}
                        </div>
                    ) : null}
                    {right}
                </div>
            </div>
            {children}
        </div>
    );
}

// Per-model throughput history shares the activity range and bucket boundaries.
export function ModelTpsChart({ activity }: { activity: ActivityResponse }) {
    const [hidden, setHidden] = useState<Set<string>>(new Set());
    const models = useMemo(
        () =>
            Array.from(
                new Set(activity.points.flatMap((point) => Object.keys(point.model_tps ?? {}))),
            ).sort(),
        [activity],
    );
    // A filter can leave only previously hidden models; keep at least one line visible.
    const hiddenModels = models.every((model) => hidden.has(model)) ? new Set<string>() : hidden;
    const series = models.map((model, index) => ({
        model,
        key: `model${index}`,
        color: STACK_COLORS[index % STACK_COLORS.length],
    }));
    const data = activity.points.map((point) => {
        const date = new Date(point.ts);
        const axis =
            activity.granularity === "hour"
                ? date.toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                  })
                : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        return {
            axis,
            label: date.toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
                ...(activity.granularity === "hour" ? { hour: "2-digit", minute: "2-digit" } : {}),
            }),
            ...Object.fromEntries(
                series.map(({ model, key }) => [key, point.model_tps?.[model] ?? null]),
            ),
        };
    });
    const visibleCount = models.filter((model) => !hiddenModels.has(model)).length;
    return (
        <section
            className="border-ink-700 bg-ink-900 min-w-0 overflow-hidden rounded-2xl border px-3 py-5 sm:px-5"
            aria-label="Model TPS over time"
        >
            <div className="mb-5 flex items-start justify-between gap-3">
                <div>
                    <h3 className="text-fog-100 text-sm font-semibold">TPS over time</h3>
                    <p className="text-fog-400 mt-1 text-xs">
                        Average output tokens per second, per model ·{" "}
                        {activity.granularity === "hour" ? "Hourly" : "Daily"} averages
                    </p>
                </div>
                <span className="text-fog-500 font-mono text-xs">t/s</span>
            </div>
            {series.length ? (
                <>
                    <div
                        className="mb-5 flex flex-wrap gap-x-4 gap-y-2"
                        aria-label="Toggle model lines"
                    >
                        {series.map(({ model, color }) => (
                            <button
                                key={model}
                                type="button"
                                aria-pressed={!hiddenModels.has(model)}
                                disabled={!hiddenModels.has(model) && visibleCount === 1}
                                onClick={() =>
                                    setHidden((current) => {
                                        const next = new Set(current);
                                        if (next.has(model)) next.delete(model);
                                        else next.add(model);
                                        return next;
                                    })
                                }
                                className={`text-fog-300 focus-visible:outline-brand-400 inline-flex max-w-full items-center gap-2 rounded py-1 text-xs focus-visible:outline-2 ${hiddenModels.has(model) ? "opacity-40" : ""}`}
                            >
                                <span
                                    className="h-2 w-2 shrink-0 rounded-full"
                                    style={{ backgroundColor: color }}
                                />
                                <span className="truncate font-mono" title={model}>
                                    {model.replace(/^(gpt-|claude-)/, "")}
                                </span>
                            </button>
                        ))}
                    </div>
                    <div className="h-[280px] w-full sm:h-[320px]">
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart
                                data={data}
                                margin={{ top: 8, right: 10, left: 0, bottom: 8 }}
                                accessibilityLayer
                            >
                                <CartesianGrid
                                    stroke={GRID}
                                    strokeDasharray="3 5"
                                    vertical={false}
                                />
                                <XAxis
                                    dataKey="axis"
                                    stroke={AXIS}
                                    tickLine={false}
                                    axisLine={false}
                                    minTickGap={32}
                                    tick={{ fontSize: 11 }}
                                    dy={8}
                                />
                                <YAxis
                                    stroke={AXIS}
                                    tickLine={false}
                                    axisLine={false}
                                    width={44}
                                    tick={{ fontSize: 11 }}
                                    domain={[0, "auto"]}
                                    tickFormatter={(value: number) =>
                                        Number(value.toFixed(1)).toString()
                                    }
                                />
                                <Tooltip
                                    content={({ active, payload }) => {
                                        if (!active || !payload?.length) return null;
                                        const measured = payload.filter(
                                            (entry) => entry.value != null,
                                        );
                                        if (!measured.length) return null;
                                        return (
                                            <div className="border-ink-600 bg-ink-950 text-fog-200 max-w-[300px] rounded-xl border p-3 text-xs shadow-xl">
                                                <p className="text-fog-400 mb-2">
                                                    {String(payload[0]?.payload.label ?? "")}
                                                </p>
                                                {measured.map((entry) => (
                                                    <div
                                                        key={String(entry.dataKey)}
                                                        className="flex items-center justify-between gap-5 py-1"
                                                    >
                                                        <span
                                                            className="truncate"
                                                            style={{ color: entry.color }}
                                                        >
                                                            {entry.name}
                                                        </span>
                                                        <span className="shrink-0 font-mono tabular-nums">
                                                            {Number(entry.value).toFixed(2)} t/s
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                        );
                                    }}
                                />
                                {series.map(({ model, key, color }) => (
                                    <Line
                                        key={model}
                                        dataKey={key}
                                        name={model}
                                        stroke={color}
                                        strokeWidth={2.5}
                                        type="monotone"
                                        dot={{ r: 2, strokeWidth: 0, fill: color }}
                                        activeDot={{ r: 5, strokeWidth: 2, fill: color }}
                                        hide={hiddenModels.has(model)}
                                        connectNulls={false}
                                        isAnimationActive={false}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                    <p className="text-fog-500 mt-3 text-xs">
                        Select a model above to hide or show its line. Gaps indicate no measured
                        requests.
                    </p>
                </>
            ) : (
                <p className="text-fog-400 py-12 text-center text-sm">
                    No TPS measurements in this range.
                </p>
            )}
        </section>
    );
}
