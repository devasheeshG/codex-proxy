"use client";

// ---------------------------------------------------------------------------
// Users page: each user owns one or more API keys. Create/edit/delete users,
// set per-user rate limits and token budgets, manage their keys (one-time
// secret reveal with native-config setup), and see month-to-date usage.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useState } from "react";
import {
    Check,
    ChevronRight,
    KeyRound,
    ListChecks,
    Pencil,
    Plus,
    Search,
    Trash2,
    X,
} from "lucide-react";
import { api, API_BASE_URL, ApiError } from "@/lib/api";
import { ApiKey, ReasoningLevel, RequestMode, User } from "@/lib/types";
import { formatDateTime, formatNumber, formatTokens, formatUsd } from "@/lib/format";
import {
    Badge,
    Button,
    BulkPriorityBar,
    Card,
    ConfirmDialog,
    CopyButton,
    EmptyState,
    ErrorState,
    Field,
    LoadingState,
    Modal,
    Spinner,
    StatusToggle,
    TextInput,
} from "@/components/ui";

// Parse an optional non-negative integer limit field; blank -> null (no limit).
function parseLimitInput(value: string): number | null {
    const trimmed = value.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
}

function parseMoneyInput(value: string): number | null {
    const trimmed = value.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    return Number.isFinite(n) && n >= 0 ? n : null;
}

// Render the small "rate / budget" limit chips shared by users and keys.
function LimitBadges({ rate, budget }: { rate: number | null; budget: number | null }) {
    if (!rate && !budget) return null;
    return (
        <span className="inline-flex flex-wrap gap-1.5">
            {rate ? <Badge tone="neutral">{formatNumber(rate)}/min</Badge> : null}
            {budget ? <Badge tone="neutral">{formatTokens(budget)} tok/mo</Badge> : null}
        </span>
    );
}

function UserLimitBadges({ user }: { user: User }) {
    const limits = [
        user.rate_limit_per_minute ? `${formatNumber(user.rate_limit_per_minute)}/min` : null,
        user.monthly_token_budget ? `${formatTokens(user.monthly_token_budget)} tok/mo` : null,
        user.lifetime_token_budget
            ? `${formatTokens(user.lifetime_token_budget)} tok lifetime`
            : null,
        user.monthly_spend_budget_usd ? `${formatUsd(user.monthly_spend_budget_usd)}/mo` : null,
        user.lifetime_spend_budget_usd
            ? `${formatUsd(user.lifetime_spend_budget_usd)} lifetime`
            : null,
    ].filter((limit): limit is string => limit !== null);
    if (limits.length === 0) return null;
    return (
        <span className="inline-flex flex-wrap gap-1.5">
            {limits.map((limit) => (
                <Badge key={limit} tone="neutral">
                    {limit}
                </Badge>
            ))}
        </span>
    );
}

function BudgetBar({ used, budget }: { used: number; budget: number | null }) {
    if (!budget || budget <= 0) return null;
    const percentage = Math.min(Math.max(used / budget, 0), 1);
    return (
        <div className="bg-ink-700 mt-2 h-1.5 overflow-hidden rounded-full">
            <div
                className={`h-full rounded-full transition-all ${percentage >= 0.9 ? "bg-bad-500" : percentage >= 0.7 ? "bg-warn-500" : "bg-brand-500"}`}
                style={{ width: `${percentage * 100}%` }}
            />
        </div>
    );
}

function MonthlyUsage({ user }: { user: User }) {
    const tokensUsed = Math.max(0, user.monthly_tokens_used);
    const spendUsed = Math.max(0, user.monthly_spend_usd);
    return (
        <div className="border-ink-700 bg-ink-900/45 mt-4 rounded-lg border p-4">
            <div className="flex items-center justify-between gap-3">
                <div className="text-fog-200 text-sm font-medium">This month</div>
                <div className="text-fog-400 text-xs">
                    Resets {formatDateTime(user.monthly_reset_at)}
                </div>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="bg-ink-950/35 rounded-md p-3">
                    <div className="flex items-baseline justify-between gap-2">
                        <span className="text-fog-400 text-[11px] tracking-wider uppercase">
                            Tokens
                        </span>
                        <span className="text-fog-100 font-mono text-sm font-semibold tabular-nums">
                            {formatTokens(tokensUsed)}
                            {user.monthly_token_budget
                                ? ` / ${formatTokens(user.monthly_token_budget)}`
                                : ""}
                        </span>
                    </div>
                    <BudgetBar used={tokensUsed} budget={user.monthly_token_budget} />
                </div>
                <div className="bg-ink-950/35 rounded-md p-3">
                    <div className="flex items-baseline justify-between gap-2">
                        <span className="text-fog-400 text-[11px] tracking-wider uppercase">
                            Spend
                        </span>
                        <span className="text-fog-100 font-mono text-sm font-semibold tabular-nums">
                            {formatUsd(spendUsed)}
                            {user.monthly_spend_budget_usd
                                ? ` / ${formatUsd(user.monthly_spend_budget_usd)}`
                                : ""}
                        </span>
                    </div>
                    <BudgetBar used={spendUsed} budget={user.monthly_spend_budget_usd} />
                </div>
            </div>
        </div>
    );
}

const REQUEST_MODES: RequestMode[] = ["standard", "fast", "ultrafast"];
const requestModeLabel = (mode: RequestMode) => (mode === "ultrafast" ? "UltraFast" : mode);
const REASONING_LEVELS: ReasoningLevel[] = [
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
];

function PolicyBadges({ user }: { user: User }) {
    return (
        <span className="inline-flex flex-wrap gap-1.5">
            <Badge tone="neutral">
                {user.allowed_request_modes.map(requestModeLabel).join(" + ")}
            </Badge>
            <Badge tone="neutral">
                {user.allowed_reasoning_levels.length === REASONING_LEVELS.length
                    ? "all thinking"
                    : `${user.allowed_reasoning_levels.length} thinking`}
            </Badge>
            <Badge tone="neutral">
                {user.allowed_models === null
                    ? "all models"
                    : `${user.allowed_models.length} model${user.allowed_models.length === 1 ? "" : "s"}`}
            </Badge>
            {Object.keys(user.model_overrides).length > 0 ? (
                <Badge tone="brand">
                    {Object.keys(user.model_overrides).length} model rewrite
                    {Object.keys(user.model_overrides).length === 1 ? "" : "s"}
                </Badge>
            ) : null}
        </span>
    );
}

function ChoiceGroup<T extends string>({
    values,
    selected,
    onChange,
}: {
    values: T[];
    selected: T[];
    onChange: (values: T[]) => void;
}) {
    const toggle = (value: T) => {
        onChange(
            selected.includes(value)
                ? selected.filter((candidate) => candidate !== value)
                : values.filter((candidate) => selected.includes(candidate) || candidate === value),
        );
    };
    return (
        <div className="flex flex-wrap gap-2">
            {values.map((value) => (
                <label
                    key={value}
                    className="border-ink-700 bg-ink-900/50 text-fog-200 flex items-center gap-2 rounded-md border px-3 py-2 text-sm capitalize"
                >
                    <input
                        type="checkbox"
                        checked={selected.includes(value)}
                        onChange={() => toggle(value)}
                        className="border-ink-600 bg-ink-900 accent-brand-500 h-4 w-4 rounded"
                    />
                    {value === "ultrafast" ? "UltraFast" : value}
                </label>
            ))}
        </div>
    );
}

function ModelPolicyEditor({
    options,
    value,
    onChange,
}: {
    options: string[];
    value: string[] | null;
    onChange: (value: string[] | null) => void;
}) {
    const [query, setQuery] = useState("");
    const [customModel, setCustomModel] = useState("");
    const unrestricted = value === null;
    const selected = value ?? [];
    const available = Array.from(new Set([...options, ...selected])).sort();
    const filtered = available.filter((model) =>
        model.toLowerCase().includes(query.trim().toLowerCase()),
    );

    const toggleModel = (model: string) => {
        const next = selected.includes(model)
            ? selected.filter((candidate) => candidate !== model)
            : [...selected, model].sort();
        onChange(next);
    };

    const addCustomModel = () => {
        const model = customModel.trim().toLowerCase();
        if (!model) return;
        onChange(Array.from(new Set([...selected, model])).sort());
        setCustomModel("");
    };

    return (
        <div className="border-ink-700 bg-ink-900/40 overflow-hidden rounded-lg border">
            <label className="border-ink-700 flex cursor-pointer items-start gap-3 border-b px-3.5 py-3">
                <input
                    type="checkbox"
                    checked={unrestricted}
                    onChange={(event) => onChange(event.target.checked ? null : [...options])}
                    className="border-ink-600 bg-ink-900 accent-brand-500 mt-0.5 h-4 w-4 rounded"
                />
                <span>
                    <span className="text-fog-100 block text-sm font-medium">
                        Allow every model
                    </span>
                    <span className="text-fog-400 mt-0.5 block text-xs leading-relaxed">
                        Automatically includes models added to the pooled accounts later.
                    </span>
                </span>
            </label>

            {!unrestricted ? (
                <div className="space-y-3 p-3.5">
                    <div className="flex items-center justify-between gap-3">
                        <span className="text-fog-300 text-xs font-medium">
                            {selected.length} selected
                        </span>
                        {selected.length > 0 ? (
                            <button
                                type="button"
                                onClick={() => onChange([])}
                                className="text-fog-400 hover:text-fog-100 text-xs transition-colors"
                            >
                                Clear selection
                            </button>
                        ) : null}
                    </div>

                    {available.length > 0 ? (
                        <>
                            <div className="relative">
                                <Search
                                    size={14}
                                    className="text-fog-500 pointer-events-none absolute top-1/2 left-3 -translate-y-1/2"
                                />
                                <input
                                    value={query}
                                    onChange={(event) => setQuery(event.target.value)}
                                    placeholder="Search known models…"
                                    className="border-ink-600 bg-ink-900 text-fog-100 placeholder:text-fog-500 focus:border-brand-400 w-full rounded-md border py-2 pr-3 pl-9 font-mono text-xs outline-none"
                                />
                            </div>
                            <div className="grid max-h-48 grid-cols-1 gap-1.5 overflow-y-auto sm:grid-cols-2">
                                {filtered.map((model) => (
                                    <label
                                        key={model}
                                        className="border-ink-700 bg-ink-900/70 text-fog-200 hover:border-ink-600 flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 font-mono text-xs transition-colors"
                                    >
                                        <input
                                            type="checkbox"
                                            checked={selected.includes(model)}
                                            onChange={() => toggleModel(model)}
                                            className="border-ink-600 bg-ink-900 accent-brand-500 h-3.5 w-3.5 rounded"
                                        />
                                        <span className="truncate">{model}</span>
                                    </label>
                                ))}
                                {filtered.length === 0 ? (
                                    <p className="text-fog-500 col-span-full py-3 text-center text-xs">
                                        No matching known models.
                                    </p>
                                ) : null}
                            </div>
                        </>
                    ) : (
                        <p className="text-fog-400 text-xs leading-relaxed">
                            No model catalog has been cached yet. Add an exact model ID below.
                        </p>
                    )}

                    <div className="flex gap-2">
                        <div className="relative flex-1">
                            {customModel ? (
                                <button
                                    type="button"
                                    onClick={() => setCustomModel("")}
                                    className="text-fog-500 hover:text-fog-200 absolute top-1/2 right-2.5 -translate-y-1/2"
                                    aria-label="Clear custom model"
                                >
                                    <X size={13} />
                                </button>
                            ) : null}
                            <input
                                value={customModel}
                                onChange={(event) => setCustomModel(event.target.value)}
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                        event.preventDefault();
                                        addCustomModel();
                                    }
                                }}
                                placeholder="Add exact model ID"
                                className="border-ink-600 bg-ink-900 text-fog-100 placeholder:text-fog-500 focus:border-brand-400 w-full rounded-md border px-3 py-2 pr-8 font-mono text-xs outline-none"
                            />
                        </div>
                        <Button
                            type="button"
                            variant="ghost"
                            disabled={!customModel.trim()}
                            onClick={addCustomModel}
                        >
                            <Plus size={14} />
                            Add
                        </Button>
                    </div>
                    {selected.length === 0 ? (
                        <p className="text-warn-500 text-xs">
                            Select or add at least one model before saving.
                        </p>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
}

function ModelOverrideEditor({
    options,
    value,
    onChange,
}: {
    options: string[];
    value: Record<string, string>;
    onChange: (value: Record<string, string>) => void;
}) {
    const [source, setSource] = useState("");
    const [target, setTarget] = useState("");
    const entries = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
    const add = () => {
        const requested = source.trim().toLowerCase();
        const upstream = target.trim().toLowerCase();
        if (!requested || !upstream || requested === upstream) return;
        onChange({ ...value, [requested]: upstream });
        setSource("");
        setTarget("");
    };

    return (
        <div className="border-ink-700 bg-ink-900/40 overflow-hidden rounded-lg border">
            {entries.length > 0 ? (
                <div className="border-ink-700 divide-ink-700 divide-y border-b">
                    {entries.map(([requested, upstream]) => (
                        <div
                            key={requested}
                            className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto] items-center gap-2 px-3.5 py-2.5"
                        >
                            <code className="text-fog-200 truncate text-xs">{requested}</code>
                            <span className="text-brand-300 text-xs" aria-hidden="true">
                                →
                            </span>
                            <code className="text-fog-100 truncate text-xs">{upstream}</code>
                            <button
                                type="button"
                                onClick={() => {
                                    const next = { ...value };
                                    delete next[requested];
                                    onChange(next);
                                }}
                                className="text-fog-500 hover:text-bad-500 rounded p-1 transition-colors"
                                aria-label={`Remove rewrite from ${requested}`}
                            >
                                <Trash2 size={14} />
                            </button>
                        </div>
                    ))}
                </div>
            ) : (
                <p className="text-fog-400 px-3.5 pt-3 text-xs">
                    Requests currently keep their original model ID.
                </p>
            )}
            <div className="grid gap-2 p-3.5 sm:grid-cols-[1fr_auto_1fr_auto] sm:items-center">
                <input
                    list="known-model-overrides"
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    placeholder="Requested model"
                    className="border-ink-600 bg-ink-900 text-fog-100 placeholder:text-fog-500 focus:border-brand-400 min-w-0 rounded-md border px-3 py-2 font-mono text-xs outline-none"
                />
                <span className="text-brand-300 hidden text-center text-sm sm:block">→</span>
                <input
                    list="known-model-overrides"
                    value={target}
                    onChange={(event) => setTarget(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter") {
                            event.preventDefault();
                            add();
                        }
                    }}
                    placeholder="Upstream model"
                    className="border-ink-600 bg-ink-900 text-fog-100 placeholder:text-fog-500 focus:border-brand-400 min-w-0 rounded-md border px-3 py-2 font-mono text-xs outline-none"
                />
                <Button
                    type="button"
                    variant="ghost"
                    disabled={!source.trim() || !target.trim() || source.trim() === target.trim()}
                    onClick={add}
                >
                    <Plus size={14} /> Add
                </Button>
                <datalist id="known-model-overrides">
                    {options.map((model) => (
                        <option key={model} value={model} />
                    ))}
                </datalist>
            </div>
        </div>
    );
}

export default function UsersPage() {
    const [users, setUsers] = useState<User[] | null>(null);
    const [modelOptions, setModelOptions] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [busyId, setBusyId] = useState<string | null>(null);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [userSearch, setUserSearch] = useState("");
    const [savedPriorities, setSavedPriorities] = useState<Record<string, number>>({});
    const [draggedId, setDraggedId] = useState<string | null>(null);
    const [savingPriority, setSavingPriority] = useState(false);
    const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
    const [selectionMode, setSelectionMode] = useState(false);
    const [bulkPriority, setBulkPriority] = useState("1");
    const [applyingBulkPriority, setApplyingBulkPriority] = useState(false);

    const [showCreate, setShowCreate] = useState(false);
    const [editTarget, setEditTarget] = useState<User | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
    const [deleting, setDeleting] = useState(false);

    // One-time key reveal (after creating a key).
    const [revealed, setRevealed] = useState<{
        name: string;
        label: string;
        secret: string;
    } | null>(null);

    const load = useCallback(async (background = false) => {
        if (!background) setLoading(true);
        if (!background) setError(null);
        try {
            const loaded = await api.users();
            setUsers(loaded);
            setSavedPriorities(
                Object.fromEntries(loaded.map((user) => [user.id, user.priority ?? 4])),
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load users.");
        } finally {
            if (!background) setLoading(false);
        }
    }, []);

    const refreshModelOptions = useCallback(async () => {
        try {
            setModelOptions(await api.userModelOptions());
        } catch {
            // Model choices are optional; keep the editor usable with exact IDs.
        }
    }, []);

    useEffect(() => {
        void load();
        void refreshModelOptions();
    }, [load, refreshModelOptions]);

    const normalizedUserSearch = userSearch.trim().toLocaleLowerCase();
    const visibleUsers =
        users?.filter((user) => user.name.toLocaleLowerCase().includes(normalizedUserSearch)) ?? [];
    const priorityLanes = Array.from(new Set(visibleUsers.map((user) => user.priority ?? 4))).sort(
        (a, b) => a - b,
    );
    const priorityDirty =
        users?.some((user) => savedPriorities[user.id] !== (user.priority ?? 4)) ?? false;
    const toggleUserSelection = (id: string) => {
        setSelectedUserIds((current) =>
            current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
        );
    };
    const applyBulkPriority = async () => {
        const priority = Number(bulkPriority);
        if (!users || selectedUserIds.length === 0 || !Number.isInteger(priority) || priority < 1)
            return;
        setApplyingBulkPriority(true);
        setError(null);
        try {
            const updated = await api.bulkSetUserPriority(selectedUserIds, priority);
            setUsers(updated);
            setSavedPriorities(
                Object.fromEntries(updated.map((user) => [user.id, user.priority ?? 4])),
            );
            setSelectedUserIds([]);
            setSelectionMode(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not set user priority.");
        } finally {
            setApplyingBulkPriority(false);
        }
    };
    const exitSelectionMode = () => {
        setSelectedUserIds([]);
        setSelectionMode(false);
    };
    const toggleVisibleSelection = () => {
        if (selectedUserIds.length === visibleUsers.length) {
            setSelectedUserIds([]);
            return;
        }
        setSelectedUserIds(visibleUsers.map((user) => user.id));
    };
    const dropUser = (priority: number) => {
        if (!users || !draggedId) {
            setDraggedId(null);
            return;
        }
        setUsers(users.map((user) => (user.id === draggedId ? { ...user, priority } : user)));
        setDraggedId(null);
    };
    const savePriority = async () => {
        if (!users || !priorityDirty) return;
        setSavingPriority(true);
        setError(null);
        try {
            const changed = users.filter(
                (user) => savedPriorities[user.id] !== (user.priority ?? 4),
            );
            await Promise.all(
                changed.map((user) => api.updateUser(user.id, { priority: user.priority ?? 4 })),
            );
            setSavedPriorities(
                Object.fromEntries(users.map((user) => [user.id, user.priority ?? 4])),
            );
            await load(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save user priority order.");
        } finally {
            setSavingPriority(false);
        }
    };

    const toggleActive = async (user: User) => {
        setBusyId(user.id);
        try {
            const updated = await api.updateUser(user.id, { active: !user.active });
            setUsers(
                (current) =>
                    current?.map((candidate) =>
                        candidate.id === updated.id ? updated : candidate,
                    ) ?? current,
            );
        } catch (err) {
            setError(err instanceof ApiError ? err.message : "Update failed.");
        } finally {
            setBusyId(null);
        }
    };

    const confirmDelete = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        try {
            await api.deleteUser(deleteTarget.id);
            setUsers(
                (current) => current?.filter((user) => user.id !== deleteTarget.id) ?? current,
            );
            setExpandedId((current) => (current === deleteTarget.id ? null : current));
            setDeleteTarget(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Delete failed.");
        } finally {
            setDeleting(false);
        }
    };

    return (
        <div className="space-y-6">
            <header className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h1 className="text-fog-100 font-serif text-2xl font-semibold tracking-tight">
                        Users
                    </h1>
                    <p className="text-fog-400 mt-0.5 text-sm">
                        Manage API keys, usage limits, request modes, thinking levels, and model
                        access per user.
                    </p>
                </div>
                <div className="flex w-full flex-wrap items-center gap-2 sm:ml-auto sm:w-auto sm:justify-end">
                    <div className="relative min-w-52 flex-1 sm:w-64 sm:flex-none">
                        <Search
                            size={15}
                            aria-hidden="true"
                            className="text-fog-400 pointer-events-none absolute top-1/2 left-3 -translate-y-1/2"
                        />
                        <TextInput
                            type="search"
                            value={userSearch}
                            onChange={(event) => setUserSearch(event.target.value)}
                            placeholder="Search users…"
                            aria-label="Search users by name"
                            className="pl-9"
                        />
                    </div>
                    <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                        <Button
                            variant="ghost"
                            onClick={() => {
                                void load(true);
                                void refreshModelOptions();
                            }}
                        >
                            Refresh
                        </Button>
                        <Button
                            variant={selectionMode ? "primary" : "ghost"}
                            onClick={() =>
                                selectionMode ? exitSelectionMode() : setSelectionMode(true)
                            }
                        >
                            <ListChecks size={15} aria-hidden="true" />
                            {selectionMode ? "Selecting" : "Bulk priority"}
                        </Button>
                        <Button
                            variant="primary"
                            disabled={!priorityDirty || savingPriority}
                            onClick={() => void savePriority()}
                        >
                            {savingPriority ? <Spinner /> : null} Save priority changes
                        </Button>
                        <Button variant="primary" onClick={() => setShowCreate(true)}>
                            Create user
                        </Button>
                    </div>
                </div>
            </header>

            {selectionMode ? (
                <BulkPriorityBar
                    entityLabel="user"
                    selectedCount={selectedUserIds.length}
                    visibleCount={visibleUsers.length}
                    priority={bulkPriority}
                    onPriorityChange={setBulkPriority}
                    onSelectVisible={toggleVisibleSelection}
                    onApply={() => void applyBulkPriority()}
                    onCancel={() => setSelectedUserIds([])}
                    onClose={exitSelectionMode}
                    applying={applyingBulkPriority}
                />
            ) : null}

            {error ? (
                <div
                    role="alert"
                    className="border-bad-500/30 bg-bad-500/10 text-bad-500 rounded-md border px-3 py-2 text-sm"
                >
                    {error}
                </div>
            ) : null}

            {loading ? (
                <Card>
                    <LoadingState />
                </Card>
            ) : error && !users ? (
                <Card>
                    <ErrorState message={error} onRetry={() => void load()} />
                </Card>
            ) : users && users.length > 0 && visibleUsers.length === 0 ? (
                <Card>
                    <EmptyState message="No users match this search." />
                </Card>
            ) : users && users.length > 0 ? (
                <div className="space-y-8">
                    {priorityLanes.map((priority) => {
                        const laneUsers = visibleUsers.filter(
                            (user) => (user.priority ?? 4) === priority,
                        );
                        return (
                            <section
                                key={priority}
                                aria-labelledby={`user-priority-${priority}`}
                                onDragOver={(event) => event.preventDefault()}
                                onDrop={() => dropUser(priority)}
                                className={`rounded-xl border p-4 transition-colors ${draggedId ? "border-brand-500/50 bg-brand-500/5" : "border-ink-700 bg-ink-950/20"}`}
                            >
                                <div className="mb-4 flex items-center justify-between gap-3">
                                    <div>
                                        <h2
                                            id={`user-priority-${priority}`}
                                            className="text-fog-100 text-sm font-semibold tracking-[0.18em] uppercase"
                                        >
                                            Priority {priority}
                                        </h2>
                                        <p className="text-fog-500 mt-1 text-xs">
                                            {laneUsers.length} user
                                            {laneUsers.length === 1 ? "" : "s"} · drag here to
                                            assign
                                        </p>
                                    </div>
                                    {draggedId ? (
                                        <span className="text-brand-300 text-xs">
                                            Release to move
                                        </span>
                                    ) : null}
                                </div>
                                <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
                                    {laneUsers.map((user) => {
                                        const busy = busyId === user.id;
                                        const expanded = expandedId === user.id;
                                        return (
                                            <div
                                                key={user.id}
                                                draggable
                                                aria-label={`Drag ${user.name} to another priority lane`}
                                                aria-grabbed={draggedId === user.id}
                                                onDragStart={() => setDraggedId(user.id)}
                                                onDragEnd={() => setDraggedId(null)}
                                                onDragOver={(event) => event.preventDefault()}
                                                onDrop={(event) => {
                                                    event.stopPropagation();
                                                    dropUser(priority);
                                                }}
                                                className={
                                                    draggedId === user.id ? "opacity-60" : ""
                                                }
                                            >
                                                <Card
                                                    className={`cursor-grab overflow-hidden transition-shadow active:cursor-grabbing ${selectedUserIds.includes(user.id) ? "border-brand-400/80 shadow-[0_0_0_2px_color-mix(in_srgb,var(--color-brand-500)_35%,transparent)]" : ""}`}
                                                >
                                                    <div className="flex">
                                                        <div
                                                            aria-hidden
                                                            className={`w-1 shrink-0 transition-colors ${user.active ? "bg-good-500" : "bg-bad-500"}`}
                                                        />
                                                        <div className="min-w-0 flex-1">
                                                            <div className="p-5 sm:p-6">
                                                                {/* Header: name + limits + active toggle */}
                                                                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
                                                                    <div className="min-w-0">
                                                                        <div className="flex flex-wrap items-center gap-2">
                                                                            {selectionMode ? (
                                                                                <button
                                                                                    type="button"
                                                                                    onClick={(
                                                                                        event,
                                                                                    ) => {
                                                                                        event.stopPropagation();
                                                                                        toggleUserSelection(
                                                                                            user.id,
                                                                                        );
                                                                                    }}
                                                                                    aria-pressed={selectedUserIds.includes(
                                                                                        user.id,
                                                                                    )}
                                                                                    aria-label={`Select ${user.name}`}
                                                                                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${selectedUserIds.includes(user.id) ? "border-brand-400 bg-brand-500 text-ink-950" : "border-ink-600 bg-ink-900 hover:border-brand-400 text-transparent"}`}
                                                                                >
                                                                                    <Check
                                                                                        size={13}
                                                                                        strokeWidth={
                                                                                            3
                                                                                        }
                                                                                        aria-hidden="true"
                                                                                    />
                                                                                </button>
                                                                            ) : null}
                                                                            <span className="text-fog-100 text-[15px] font-semibold tracking-tight">
                                                                                {user.name}
                                                                            </span>
                                                                            <Badge tone="neutral">
                                                                                Priority{" "}
                                                                                {user.priority ?? 4}
                                                                            </Badge>
                                                                            <UserLimitBadges
                                                                                user={user}
                                                                            />
                                                                            <PolicyBadges
                                                                                user={user}
                                                                            />
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-xs">
                                                                            Added{" "}
                                                                            {formatDateTime(
                                                                                user.created_at,
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                    <StatusToggle
                                                                        on={user.active}
                                                                        busy={busy}
                                                                        onClick={() =>
                                                                            void toggleActive(user)
                                                                        }
                                                                        srLabel={
                                                                            user.active
                                                                                ? "Deactivate user"
                                                                                : "Activate user"
                                                                        }
                                                                    />
                                                                </div>

                                                                {/* Stats row */}
                                                                <div className="bg-ink-700 mt-4 grid gap-px overflow-hidden rounded-lg sm:grid-cols-2 lg:grid-cols-2">
                                                                    <div className="bg-ink-900 px-4 py-3">
                                                                        <div className="text-fog-100 font-mono text-base font-semibold tracking-tight tabular-nums">
                                                                            {formatTokens(
                                                                                user.total_tokens,
                                                                            )}
                                                                            {user.lifetime_token_budget
                                                                                ? ` / ${formatTokens(user.lifetime_token_budget)}`
                                                                                : ""}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-[11px] tracking-wider uppercase">
                                                                            All-time tokens
                                                                        </div>
                                                                    </div>
                                                                    <div className="bg-ink-900 px-4 py-3">
                                                                        <div className="text-fog-100 font-mono text-base font-semibold tracking-tight tabular-nums">
                                                                            {formatUsd(
                                                                                user.total_spend_usd,
                                                                            )}
                                                                            {user.lifetime_spend_budget_usd
                                                                                ? ` / ${formatUsd(user.lifetime_spend_budget_usd)}`
                                                                                : ""}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-[11px] tracking-wider uppercase">
                                                                            All-time spend
                                                                        </div>
                                                                    </div>
                                                                    <div className="bg-ink-900 px-4 py-3">
                                                                        <div className="text-fog-100 font-mono text-base font-semibold tracking-tight tabular-nums">
                                                                            {formatNumber(
                                                                                user.total_requests,
                                                                            )}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-[11px] tracking-wider uppercase">
                                                                            Requests
                                                                        </div>
                                                                    </div>
                                                                    <div className="bg-ink-900 px-4 py-3">
                                                                        <div className="text-fog-100 font-mono text-base font-semibold tracking-tight tabular-nums">
                                                                            {formatDateTime(
                                                                                user.last_used_at,
                                                                            )}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-[11px] tracking-wider uppercase">
                                                                            Last used
                                                                        </div>
                                                                    </div>
                                                                </div>

                                                                <MonthlyUsage user={user} />

                                                                {/* Keys toggle + actions */}
                                                                <div className="border-ink-700 mt-4 flex flex-wrap items-center justify-between gap-2 border-t pt-3.5">
                                                                    <button
                                                                        onClick={() =>
                                                                            setExpandedId(
                                                                                expanded
                                                                                    ? null
                                                                                    : user.id,
                                                                            )
                                                                        }
                                                                        className="border-ink-700 text-fog-300 hover:bg-ink-800 hover:text-fog-100 hover:border-ink-600 inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors"
                                                                    >
                                                                        <KeyRound className="h-3.5 w-3.5" />
                                                                        <span className="font-mono tabular-nums">
                                                                            {user.key_count}
                                                                        </span>{" "}
                                                                        key
                                                                        {user.key_count === 1
                                                                            ? ""
                                                                            : "s"}
                                                                        <ChevronRight
                                                                            className={`text-fog-400 h-3 w-3 transition-transform ${expanded ? "rotate-90" : ""}`}
                                                                        />
                                                                    </button>
                                                                    <div className="flex items-center gap-1">
                                                                        {busy ? (
                                                                            <Spinner className="mr-1" />
                                                                        ) : null}
                                                                        <button
                                                                            onClick={() =>
                                                                                setEditTarget(user)
                                                                            }
                                                                            disabled={busy}
                                                                            className="text-fog-400 hover:bg-ink-800 hover:text-fog-100 inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors disabled:opacity-50"
                                                                            title="Edit user"
                                                                        >
                                                                            <Pencil className="h-[15px] w-[15px]" />
                                                                        </button>
                                                                        <button
                                                                            onClick={() =>
                                                                                setDeleteTarget(
                                                                                    user,
                                                                                )
                                                                            }
                                                                            disabled={busy}
                                                                            className="text-fog-400 hover:bg-bad-500/10 hover:text-bad-500 inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors disabled:opacity-50"
                                                                            title="Delete user"
                                                                        >
                                                                            <Trash2 className="h-[15px] w-[15px]" />
                                                                        </button>
                                                                    </div>
                                                                </div>
                                                            </div>

                                                            {expanded ? (
                                                                <div className="border-ink-700 bg-ink-900/40 border-t p-4 sm:px-6">
                                                                    <KeysPanel
                                                                        user={user}
                                                                        onChanged={() =>
                                                                            void load(true)
                                                                        }
                                                                        onRevealed={(
                                                                            label,
                                                                            secret,
                                                                        ) =>
                                                                            setRevealed({
                                                                                name: user.name,
                                                                                label,
                                                                                secret,
                                                                            })
                                                                        }
                                                                    />
                                                                </div>
                                                            ) : null}
                                                        </div>
                                                    </div>
                                                </Card>
                                            </div>
                                        );
                                    })}
                                </div>
                            </section>
                        );
                    })}
                </div>
            ) : (
                <Card>
                    <EmptyState message="No users yet. Create one, then issue API keys." />
                </Card>
            )}

            {showCreate ? (
                <CreateUserModal
                    modelOptions={modelOptions}
                    onClose={() => setShowCreate(false)}
                    onCreated={(user) => {
                        setShowCreate(false);
                        setExpandedId(user.id);
                        setUsers((current) => (current ? [...current, user] : [user]));
                    }}
                />
            ) : null}

            {editTarget ? (
                <EditUserModal
                    user={editTarget}
                    modelOptions={modelOptions}
                    onClose={() => setEditTarget(null)}
                    onSaved={(updated) => {
                        setEditTarget(null);
                        setUsers(
                            (current) =>
                                current?.map((user) => (user.id === updated.id ? updated : user)) ??
                                current,
                        );
                    }}
                />
            ) : null}

            {deleteTarget ? (
                <ConfirmDialog
                    title="Delete user"
                    message={`Delete "${deleteTarget.name}"? All of their API keys stop working immediately and their usage history is removed. This cannot be undone.`}
                    confirmLabel="Delete"
                    busy={deleting}
                    onConfirm={() => void confirmDelete()}
                    onCancel={() => setDeleteTarget(null)}
                />
            ) : null}

            {revealed ? (
                <KeyRevealModal
                    name={revealed.name}
                    label={revealed.label}
                    secret={revealed.secret}
                    onClose={() => setRevealed(null)}
                />
            ) : null}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Per-user key management panel (shown when a user card is expanded).
// ---------------------------------------------------------------------------

function KeysPanel({
    user,
    onChanged,
    onRevealed,
}: {
    user: User;
    onChanged: () => void;
    onRevealed: (label: string, secret: string) => void;
}) {
    const [keys, setKeys] = useState<ApiKey[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);
    const [creating, setCreating] = useState(false);
    const [newLabel, setNewLabel] = useState("");
    const [newRate, setNewRate] = useState("");
    const [newBudget, setNewBudget] = useState("");
    const [editTarget, setEditTarget] = useState<ApiKey | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<ApiKey | null>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            setKeys(await api.keys(user.id));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load keys.");
        }
    }, [user.id]);

    useEffect(() => {
        void load();
    }, [load]);

    const create = async () => {
        if (!newLabel.trim()) {
            setError("Enter a label before creating a key.");
            return;
        }
        setCreating(true);
        setError(null);
        try {
            const res = await api.createKey(user.id, {
                label: newLabel,
                rate_limit_per_minute: parseLimitInput(newRate),
                monthly_token_budget: parseLimitInput(newBudget),
            });
            setNewLabel("");
            setNewRate("");
            setNewBudget("");
            setKeys((current) => (current ? [...current, res.api_key] : [res.api_key]));
            onRevealed(res.api_key.label ?? "key", res.secret);
            onChanged();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to create key.");
        } finally {
            setCreating(false);
        }
    };

    const toggle = async (key: ApiKey) => {
        setBusyId(key.id);
        try {
            const updated = await api.updateKey(user.id, key.id, { active: !key.active });
            setKeys(
                (current) =>
                    current?.map((candidate) =>
                        candidate.id === updated.id ? updated : candidate,
                    ) ?? current,
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Update failed.");
        } finally {
            setBusyId(null);
        }
    };

    const confirmDelete = async () => {
        if (!deleteTarget) return;
        setBusyId(deleteTarget.id);
        try {
            await api.deleteKey(user.id, deleteTarget.id);
            setKeys((current) => current?.filter((key) => key.id !== deleteTarget.id) ?? current);
            setDeleteTarget(null);
            onChanged();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Delete failed.");
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="space-y-3">
            {error ? (
                <div
                    role="alert"
                    className="border-bad-500/30 bg-bad-500/10 text-bad-500 rounded-md border px-3 py-2 text-xs"
                >
                    {error}
                </div>
            ) : null}

            {keys === null ? (
                <LoadingState label="Loading keys…" />
            ) : keys.length > 0 ? (
                <div className="space-y-1.5">
                    <div className="text-fog-400 mb-2 text-[11px] font-semibold tracking-wider uppercase">
                        API Keys
                    </div>
                    {keys.map((key) => (
                        <div
                            key={key.id}
                            className="border-ink-700 bg-ink-850 flex flex-col gap-3 rounded-lg border px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between"
                        >
                            <div className="flex min-w-0 items-center gap-3">
                                <div className="bg-ink-800 text-fog-400 flex h-7 w-7 shrink-0 items-center justify-center rounded-md">
                                    <KeyRound className="h-3.5 w-3.5" />
                                </div>
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-fog-100 truncate text-[13px] font-medium">
                                            {key.label || "Unlabeled (legacy)"}
                                        </span>
                                        <code className="bg-ink-800 text-fog-400 rounded px-1.5 py-0.5 font-mono text-[11px]">
                                            {key.key_prefix}…
                                        </code>
                                    </div>
                                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                        <LimitBadges
                                            rate={key.rate_limit_per_minute}
                                            budget={key.monthly_token_budget}
                                        />
                                        <span className="text-fog-400 text-[11px]">
                                            Last used {formatDateTime(key.last_used_at)}
                                        </span>
                                    </div>
                                </div>
                            </div>
                            <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                                <StatusToggle
                                    on={key.active}
                                    busy={busyId === key.id}
                                    onClick={() => void toggle(key)}
                                    onLabel="Enabled"
                                    offLabel="Disabled"
                                    srLabel={key.active ? "Disable key" : "Enable key"}
                                />
                                <button
                                    disabled={busyId === key.id}
                                    onClick={() => setEditTarget(key)}
                                    className="text-fog-400 hover:bg-ink-800 hover:text-fog-100 inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors disabled:opacity-50"
                                    title="Edit key"
                                >
                                    <Pencil className="h-3.5 w-3.5" />
                                </button>
                                <button
                                    disabled={busyId === key.id}
                                    onClick={() => setDeleteTarget(key)}
                                    className="text-fog-400 hover:bg-bad-500/10 hover:text-bad-500 inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors disabled:opacity-50"
                                    title="Delete key"
                                >
                                    <Trash2 className="h-3.5 w-3.5" />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            ) : (
                <div className="border-ink-600 text-fog-400 rounded-lg border border-dashed px-3 py-4 text-center text-xs">
                    No keys yet. Create one below.
                </div>
            )}

            {/* New key */}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_7rem_8rem_auto] sm:items-end">
                <TextInput
                    value={newLabel}
                    onChange={(e) => setNewLabel(e.target.value)}
                    placeholder="Key label (required, e.g. laptop, CI)"
                    aria-label="Required key label"
                />
                <TextInput
                    type="number"
                    min={0}
                    value={newRate}
                    onChange={(e) => setNewRate(e.target.value)}
                    placeholder="req/min"
                />
                <TextInput
                    type="number"
                    min={0}
                    value={newBudget}
                    onChange={(e) => setNewBudget(e.target.value)}
                    placeholder="tokens/mo"
                />
                <Button
                    variant="primary"
                    disabled={creating || !newLabel.trim()}
                    onClick={() => void create()}
                >
                    {creating ? <Spinner /> : null}
                    New key
                </Button>
            </div>
            <p className="text-fog-400 text-[11px]">
                Optional per-key limits: requests per minute and a monthly token budget. Leave blank
                to use the server default; set 0 for unlimited. Per-user limits (set on the user)
                apply on top, across all their keys.
            </p>

            {editTarget ? (
                <EditKeyModal
                    userId={user.id}
                    apiKey={editTarget}
                    onClose={() => setEditTarget(null)}
                    onSaved={(updated) => {
                        setEditTarget(null);
                        setKeys(
                            (current) =>
                                current?.map((key) => (key.id === updated.id ? updated : key)) ??
                                current,
                        );
                        onChanged();
                    }}
                />
            ) : null}

            {deleteTarget ? (
                <ConfirmDialog
                    title="Delete key"
                    message={`Delete the key "${deleteTarget.label ?? "Unlabeled (legacy)"}" (${deleteTarget.key_prefix}…)? It stops working immediately.`}
                    confirmLabel="Delete"
                    busy={busyId === deleteTarget.id}
                    onConfirm={() => void confirmDelete()}
                    onCancel={() => setDeleteTarget(null)}
                />
            ) : null}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Edit key modal (label + per-key rate limit and monthly token budget).
// ---------------------------------------------------------------------------

function EditKeyModal({
    userId,
    apiKey,
    onClose,
    onSaved,
}: {
    userId: string;
    apiKey: ApiKey;
    onClose: () => void;
    onSaved: (apiKey: ApiKey) => void;
}) {
    const [label, setLabel] = useState(apiKey.label ?? "");
    const [rate, setRate] = useState(
        apiKey.rate_limit_per_minute ? String(apiKey.rate_limit_per_minute) : "",
    );
    const [budget, setBudget] = useState(
        apiKey.monthly_token_budget ? String(apiKey.monthly_token_budget) : "",
    );
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setSubmitting(true);
        try {
            const updated = await api.updateKey(userId, apiKey.id, {
                label: label.trim(),
                // For keys, null (use server default) and 0 (unlimited) differ, so a blank
                // field must leave the limit UNCHANGED rather than silently forcing 0.
                rate_limit_per_minute: parseLimitInput(rate) ?? undefined,
                monthly_token_budget: parseLimitInput(budget) ?? undefined,
            });
            onSaved(updated);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Save failed.");
            setSubmitting(false);
        }
    };

    return (
        <Modal title="Edit key" onClose={onClose}>
            <form onSubmit={submit} className="space-y-4">
                <Field label="Label">
                    <TextInput
                        value={label}
                        onChange={(e) => setLabel(e.target.value)}
                        placeholder="e.g. laptop"
                    />
                </Field>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field
                        label="Rate limit (req / min)"
                        hint="0 = unlimited. Blank leaves it unchanged."
                    >
                        <TextInput
                            type="number"
                            min={0}
                            value={rate}
                            onChange={(e) => setRate(e.target.value)}
                            placeholder="unchanged"
                        />
                    </Field>
                    <Field
                        label="Monthly token budget"
                        hint="0 = unlimited. Blank leaves it unchanged."
                    >
                        <TextInput
                            type="number"
                            min={0}
                            value={budget}
                            onChange={(e) => setBudget(e.target.value)}
                            placeholder="unchanged"
                        />
                    </Field>
                </div>

                {error ? (
                    <div
                        role="alert"
                        className="border-bad-500/30 bg-bad-500/10 text-bad-500 rounded-md border px-3 py-2 text-sm"
                    >
                        {error}
                    </div>
                ) : null}

                <div className="flex justify-end gap-2 pt-1">
                    <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
                        Cancel
                    </Button>
                    <Button type="submit" variant="primary" disabled={submitting}>
                        {submitting ? <Spinner /> : null}
                        Save
                    </Button>
                </div>
            </form>
        </Modal>
    );
}

// ---------------------------------------------------------------------------
// Create user modal (name + optional per-user limits; keys issued afterwards).
// ---------------------------------------------------------------------------

function CreateUserModal({
    modelOptions,
    onClose,
    onCreated,
}: {
    modelOptions: string[];
    onClose: () => void;
    onCreated: (user: User) => void;
}) {
    const [name, setName] = useState("");
    const [priority, setPriority] = useState("1");
    const [fallbackEnabled, setFallbackEnabled] = useState(false);
    const [rate, setRate] = useState("");
    const [monthlyTokens, setMonthlyTokens] = useState("");
    const [lifetimeTokens, setLifetimeTokens] = useState("");
    const [monthlySpend, setMonthlySpend] = useState("");
    const [lifetimeSpend, setLifetimeSpend] = useState("");
    const [requestModes, setRequestModes] = useState<RequestMode[]>([...REQUEST_MODES]);
    const [reasoningLevels, setReasoningLevels] = useState<ReasoningLevel[]>([...REASONING_LEVELS]);
    const [allowedModels, setAllowedModels] = useState<string[] | null>(null);
    const [modelOverrides, setModelOverrides] = useState<Record<string, string>>({});
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setSubmitting(true);
        try {
            const user = await api.createUser(name.trim(), {
                priority: Number(priority) || 1,
                fallback_enabled: fallbackEnabled,
                rate_limit_per_minute: parseLimitInput(rate),
                monthly_token_budget: parseLimitInput(monthlyTokens),
                lifetime_token_budget: parseLimitInput(lifetimeTokens),
                monthly_spend_budget_usd: parseMoneyInput(monthlySpend),
                lifetime_spend_budget_usd: parseMoneyInput(lifetimeSpend),
                allowed_request_modes: requestModes,
                allowed_reasoning_levels: reasoningLevels,
                allowed_models: allowedModels,
                model_overrides: modelOverrides,
            });
            onCreated(user);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Create failed.");
            setSubmitting(false);
        }
    };

    return (
        <Modal title="Create user" onClose={onClose} widthClass="max-w-2xl">
            <form onSubmit={submit} className="space-y-4">
                <Field label="Name" hint="Issue one or more API keys after creating the user.">
                    <TextInput
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. Jane Doe"
                        autoFocus
                        required
                    />
                </Field>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field
                        label="User priority"
                        hint="Lower numbers are served first when requests queue."
                    >
                        <TextInput
                            type="number"
                            min={1}
                            max={1000}
                            value={priority}
                            onChange={(e) => setPriority(e.target.value)}
                        />
                    </Field>
                    <label className="text-fog-300 flex items-center gap-2 pt-7 text-sm">
                        <input
                            type="checkbox"
                            checked={fallbackEnabled}
                            onChange={(e) => setFallbackEnabled(e.target.checked)}
                        />
                        Allow API fallback providers
                    </label>
                </div>

                <Field label="Rate limit (req / min)" hint="Across every key. Blank = unlimited.">
                    <div className="max-w-52">
                        <TextInput
                            type="number"
                            min={0}
                            value={rate}
                            onChange={(e) => setRate(e.target.value)}
                            placeholder="unlimited"
                        />
                    </div>
                </Field>

                <div className="border-ink-700 bg-ink-950/30 rounded-lg border p-4">
                    <div className="text-fog-200 text-sm font-medium">Usage budgets</div>
                    <p className="text-fog-400 mt-0.5 text-xs">
                        Monthly caps reset on the first day of each UTC month. Lifetime caps are
                        one-time allowances and never reset.
                    </p>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <Field label="Monthly tokens" hint="Blank = unlimited.">
                            <TextInput
                                type="number"
                                min={0}
                                value={monthlyTokens}
                                onChange={(e) => setMonthlyTokens(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                        <Field label="Lifetime tokens" hint="One-time total. Blank = unlimited.">
                            <TextInput
                                type="number"
                                min={0}
                                value={lifetimeTokens}
                                onChange={(e) => setLifetimeTokens(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                        <Field label="Monthly spend (USD)" hint="API-equivalent value.">
                            <TextInput
                                type="number"
                                min={0}
                                step="0.01"
                                value={monthlySpend}
                                onChange={(e) => setMonthlySpend(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                        <Field label="Lifetime spend (USD)" hint="One-time API-equivalent value.">
                            <TextInput
                                type="number"
                                min={0}
                                step="0.01"
                                value={lifetimeSpend}
                                onChange={(e) => setLifetimeSpend(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                    </div>
                </div>

                <Field label="Allowed request modes" hint="Choose at least one mode.">
                    <ChoiceGroup
                        values={REQUEST_MODES}
                        selected={requestModes}
                        onChange={setRequestModes}
                    />
                </Field>
                <Field label="Allowed thinking levels" hint="Choose at least one level.">
                    <ChoiceGroup
                        values={REASONING_LEVELS}
                        selected={reasoningLevels}
                        onChange={setReasoningLevels}
                    />
                </Field>
                <Field
                    label="Allowed models"
                    hint="This checks the model the client requested, before any rewrite below."
                >
                    <ModelPolicyEditor
                        options={modelOptions}
                        value={allowedModels}
                        onChange={setAllowedModels}
                    />
                </Field>
                <Field
                    label="Server-side model rewrites"
                    hint="Rewrite an exact client model ID before account selection and upstream routing."
                >
                    <ModelOverrideEditor
                        options={modelOptions}
                        value={modelOverrides}
                        onChange={setModelOverrides}
                    />
                </Field>

                {error ? (
                    <div
                        role="alert"
                        className="border-bad-500/30 bg-bad-500/10 text-bad-500 rounded-md border px-3 py-2 text-sm"
                    >
                        {error}
                    </div>
                ) : null}

                <div className="flex justify-end gap-2 pt-1">
                    <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        variant="primary"
                        disabled={
                            submitting ||
                            !name.trim() ||
                            requestModes.length === 0 ||
                            reasoningLevels.length === 0 ||
                            (allowedModels !== null && allowedModels.length === 0)
                        }
                    >
                        {submitting ? <Spinner /> : null}
                        Create
                    </Button>
                </div>
            </form>
        </Modal>
    );
}

// ---------------------------------------------------------------------------
// Edit user modal (name + active + per-user rate limit and token budget).
// ---------------------------------------------------------------------------

function EditUserModal({
    user,
    modelOptions,
    onClose,
    onSaved,
}: {
    user: User;
    modelOptions: string[];
    onClose: () => void;
    onSaved: (user: User) => void;
}) {
    const [name, setName] = useState(user.name);
    const [active, setActive] = useState(user.active);
    const [priority, setPriority] = useState(String(user.priority ?? 1));
    const [fallbackEnabled, setFallbackEnabled] = useState(user.fallback_enabled ?? true);
    const [rate, setRate] = useState(
        user.rate_limit_per_minute ? String(user.rate_limit_per_minute) : "",
    );
    const [monthlyTokens, setMonthlyTokens] = useState(
        user.monthly_token_budget ? String(user.monthly_token_budget) : "",
    );
    const [lifetimeTokens, setLifetimeTokens] = useState(
        user.lifetime_token_budget ? String(user.lifetime_token_budget) : "",
    );
    const [monthlySpend, setMonthlySpend] = useState(
        user.monthly_spend_budget_usd ? String(user.monthly_spend_budget_usd) : "",
    );
    const [lifetimeSpend, setLifetimeSpend] = useState(
        user.lifetime_spend_budget_usd ? String(user.lifetime_spend_budget_usd) : "",
    );
    const [requestModes, setRequestModes] = useState<RequestMode[]>([
        ...user.allowed_request_modes,
    ]);
    const [reasoningLevels, setReasoningLevels] = useState<ReasoningLevel[]>([
        ...user.allowed_reasoning_levels,
    ]);
    const [allowedModels, setAllowedModels] = useState<string[] | null>(
        user.allowed_models === null ? null : [...user.allowed_models],
    );
    const [modelOverrides, setModelOverrides] = useState<Record<string, string>>({
        ...user.model_overrides,
    });
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setSubmitting(true);
        try {
            const updated = await api.updateUser(user.id, {
                name: name.trim(),
                active,
                priority: Number(priority) || 1,
                fallback_enabled: fallbackEnabled,
                rate_limit_per_minute: parseLimitInput(rate) ?? 0,
                monthly_token_budget: parseLimitInput(monthlyTokens) ?? 0,
                lifetime_token_budget: parseLimitInput(lifetimeTokens) ?? 0,
                monthly_spend_budget_usd: parseMoneyInput(monthlySpend) ?? 0,
                lifetime_spend_budget_usd: parseMoneyInput(lifetimeSpend) ?? 0,
                allowed_request_modes: requestModes,
                allowed_reasoning_levels: reasoningLevels,
                allowed_models: allowedModels,
                model_overrides: modelOverrides,
            });
            onSaved(updated);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Save failed.");
            setSubmitting(false);
        }
    };

    return (
        <Modal title={`Edit ${user.name}`} onClose={onClose} widthClass="max-w-3xl">
            <form onSubmit={submit} className="space-y-4">
                <Field label="Name">
                    <TextInput value={name} onChange={(e) => setName(e.target.value)} required />
                </Field>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field
                        label="User priority"
                        hint="Lower numbers are served first when requests queue."
                    >
                        <TextInput
                            type="number"
                            min={1}
                            max={1000}
                            value={priority}
                            onChange={(e) => setPriority(e.target.value)}
                        />
                    </Field>
                    <label className="text-fog-300 flex items-center gap-2 pt-7 text-sm">
                        <input
                            type="checkbox"
                            checked={fallbackEnabled}
                            onChange={(e) => setFallbackEnabled(e.target.checked)}
                        />
                        Allow API fallback providers
                    </label>
                </div>

                <Field
                    label="Rate limit (req / min)"
                    hint="Across all the user's keys. 0 or blank = unlimited."
                >
                    <div className="max-w-52">
                        <TextInput
                            type="number"
                            min={0}
                            value={rate}
                            onChange={(e) => setRate(e.target.value)}
                            placeholder="unlimited"
                        />
                    </div>
                </Field>

                <div className="border-ink-700 bg-ink-950/30 rounded-lg border p-4">
                    <div className="text-fog-200 text-sm font-medium">Usage budgets</div>
                    <p className="text-fog-400 mt-0.5 text-xs">
                        Monthly caps reset in UTC. Lifetime caps are one-time allowances. Set 0 or
                        leave blank for unlimited.
                    </p>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <Field label="Monthly tokens">
                            <TextInput
                                type="number"
                                min={0}
                                value={monthlyTokens}
                                onChange={(e) => setMonthlyTokens(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                        <Field label="Lifetime tokens">
                            <TextInput
                                type="number"
                                min={0}
                                value={lifetimeTokens}
                                onChange={(e) => setLifetimeTokens(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                        <Field label="Monthly spend (USD)" hint="API-equivalent value.">
                            <TextInput
                                type="number"
                                min={0}
                                step="0.01"
                                value={monthlySpend}
                                onChange={(e) => setMonthlySpend(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                        <Field label="Lifetime spend (USD)" hint="One-time API-equivalent value.">
                            <TextInput
                                type="number"
                                min={0}
                                step="0.01"
                                value={lifetimeSpend}
                                onChange={(e) => setLifetimeSpend(e.target.value)}
                                placeholder="unlimited"
                            />
                        </Field>
                    </div>
                </div>

                <Field label="Allowed request modes" hint="Choose at least one mode.">
                    <ChoiceGroup
                        values={REQUEST_MODES}
                        selected={requestModes}
                        onChange={setRequestModes}
                    />
                </Field>
                <Field
                    label="Allowed thinking levels"
                    hint="Requests must set reasoning effort when this list is restricted."
                >
                    <ChoiceGroup
                        values={REASONING_LEVELS}
                        selected={reasoningLevels}
                        onChange={setReasoningLevels}
                    />
                </Field>
                <Field
                    label="Allowed models"
                    hint="This checks the client-requested model before applying a rewrite."
                >
                    <ModelPolicyEditor
                        options={modelOptions}
                        value={allowedModels}
                        onChange={setAllowedModels}
                    />
                </Field>
                <Field
                    label="Server-side model rewrites"
                    hint="Exact per-user rewrites applied before account selection and upstream routing."
                >
                    <ModelOverrideEditor
                        options={modelOptions}
                        value={modelOverrides}
                        onChange={setModelOverrides}
                    />
                </Field>

                <label className="border-ink-700 bg-ink-900/50 flex items-center gap-2.5 rounded-md border px-3 py-2.5">
                    <input
                        type="checkbox"
                        checked={active}
                        onChange={(e) => setActive(e.target.checked)}
                        className="border-ink-600 bg-ink-900 accent-brand-500 h-4 w-4 rounded"
                    />
                    <span className="text-fog-200 text-sm">
                        Active (deactivating disables all of this user&apos;s keys)
                    </span>
                </label>

                {error ? (
                    <div
                        role="alert"
                        className="border-bad-500/30 bg-bad-500/10 text-bad-500 rounded-md border px-3 py-2 text-sm"
                    >
                        {error}
                    </div>
                ) : null}

                <div className="flex justify-end gap-2 pt-1">
                    <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        variant="primary"
                        disabled={
                            submitting ||
                            !name.trim() ||
                            requestModes.length === 0 ||
                            reasoningLevels.length === 0 ||
                            (allowedModels !== null && allowedModels.length === 0)
                        }
                    >
                        {submitting ? <Spinner /> : null}
                        Save
                    </Button>
                </div>
            </form>
        </Modal>
    );
}

// ---------------------------------------------------------------------------
// One-time key reveal with an in-place Codex config installer.
// ---------------------------------------------------------------------------

function KeyRevealModal({
    name,
    label,
    secret,
    onClose,
}: {
    name: string;
    label: string;
    secret: string;
    onClose: () => void;
}) {
    const [os, setOs] = useState<"unix" | "windows">("unix");

    // API_BASE_URL is relative ("/api") in production; prefix the current origin for a full URL.
    const baseUrl =
        API_BASE_URL.startsWith("http") || typeof window === "undefined"
            ? API_BASE_URL
            : `${window.location.origin}${API_BASE_URL}`;

    const origin = typeof window === "undefined" ? "" : window.location.origin;
    // The one-time key must never be copied into a command: command text is
    // commonly retained by shell history, process monitors, and support logs.
    // Both installers collect it separately through a hidden interactive prompt.
    const unixCmd = `curl -fsSL '${origin}/install.sh' | bash -s -- '${baseUrl}'`;
    const winCmd = `powershell -NoProfile -Command "Set-Variable CODEX_PROXY_URL '${baseUrl}'; irm '${origin}/install.ps1' | iex"`;
    const installCmd = os === "unix" ? unixCmd : winCmd;

    return (
        <Modal title="API key" onClose={onClose} widthClass="max-w-xl">
            <div className="space-y-4">
                <div
                    role="status"
                    className="border-warn-500/30 bg-warn-500/10 text-warn-500 rounded-md border px-3 py-2 text-sm"
                >
                    Copy this key now — it is shown only once and cannot be retrieved later.
                </div>

                <div>
                    <div className="text-fog-300 mb-1.5 text-xs font-medium">
                        Key &quot;{label}&quot; for {name}
                    </div>
                    <div className="flex items-center gap-2">
                        <code className="border-ink-600 bg-ink-900 text-fog-100 block flex-1 overflow-x-auto rounded-md border px-3 py-2 font-mono text-xs whitespace-nowrap">
                            {secret}
                        </code>
                        <CopyButton text={secret} label="Copy" />
                    </div>
                </div>

                <div>
                    <div className="mb-1.5 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <span className="text-fog-300 text-xs font-medium">Quick setup</span>
                            <div className="border-ink-600 flex overflow-hidden rounded-md border">
                                <button
                                    onClick={() => setOs("unix")}
                                    className={`px-2 py-0.5 text-[11px] font-medium transition-colors ${
                                        os === "unix"
                                            ? "bg-ink-700 text-fog-100"
                                            : "bg-ink-900 text-fog-400 hover:text-fog-200"
                                    }`}
                                >
                                    macOS / Linux
                                </button>
                                <button
                                    onClick={() => setOs("windows")}
                                    className={`border-ink-600 border-l px-2 py-0.5 text-[11px] font-medium transition-colors ${
                                        os === "windows"
                                            ? "bg-ink-700 text-fog-100"
                                            : "bg-ink-900 text-fog-400 hover:text-fog-200"
                                    }`}
                                >
                                    Windows
                                </button>
                            </div>
                        </div>
                        <CopyButton text={installCmd} label="Copy" />
                    </div>
                    <pre className="border-ink-600 bg-ink-900 text-fog-200 overflow-x-auto rounded-md border px-3 py-2 font-mono text-xs leading-relaxed">
                        {installCmd}
                    </pre>
                    <p className="text-fog-400 mt-1.5 text-[11px] leading-relaxed">
                        {os === "unix" ? (
                            <>
                                Paste the command, then enter the key above at the hidden prompt.
                                Updates <code className="font-mono">~/.codex/config.toml</code> so
                                the original <code className="font-mono">codex</code> command uses
                                the proxy. The key is never placed in command history or process
                                arguments.
                            </>
                        ) : (
                            <>
                                Paste into PowerShell, then enter the key above at the hidden
                                prompt. Updates{" "}
                                <code className="font-mono">%USERPROFILE%\.codex\config.toml</code>{" "}
                                in place. The key is never placed in command history or process
                                arguments.
                            </>
                        )}
                    </p>
                </div>

                <div className="flex justify-end pt-1">
                    <Button variant="primary" onClick={onClose}>
                        Done
                    </Button>
                </div>
            </div>
        </Modal>
    );
}
