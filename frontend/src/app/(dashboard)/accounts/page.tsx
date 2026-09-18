"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, Check, ListChecks } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import {
    Account,
    EgressTarget,
    OAuthStartResponse,
    ProviderHealth,
    RateLimitResetCredit,
} from "@/lib/types";
import { formatCountdown, formatDateTime, formatUsd, tierLabel } from "@/lib/format";
import {
    Badge,
    Button,
    BulkPriorityBar,
    Card,
    ConfirmDialog,
    EmptyState,
    ErrorState,
    Field,
    LoadingState,
    Modal,
    Segmented,
    SelectMenu,
    Spinner,
    TextInput,
    UsageBar,
} from "@/components/ui";

const LIMIT_RESET_MIN_WEEKLY_USAGE = 0.9;
const LIMIT_RESET_EXPIRY_WINDOW_MS = 12 * 60 * 60 * 1000;
type AccountFilter = "all" | "authenticated" | "usable";
const ACCOUNT_FILTER_OPTIONS: { value: AccountFilter; label: string }[] = [
    { value: "all", label: "All accounts" },
    { value: "authenticated", label: "Authenticated accounts" },
    { value: "usable", label: "Usable accounts" },
];

function resetCreditExpiresSoon(credit: RateLimitResetCredit): boolean {
    if (credit.status !== "available" || credit.is_supported_by_plan === false) return false;
    if (!credit.expires_at) return false;
    const remainingMs = new Date(credit.expires_at).getTime() - Date.now();
    return remainingMs > 0 && remainingMs < LIMIT_RESET_EXPIRY_WINDOW_MS;
}

function cooldownIsActive(account: Account, now = Date.now()): boolean {
    return (
        account.status === "COOLDOWN" &&
        account.cooldown_until !== null &&
        new Date(account.cooldown_until).getTime() > now
    );
}

function statusBadge(account: Account) {
    if (account.status === "DISABLED") return <Badge tone="bad">Disabled</Badge>;
    if (cooldownIsActive(account)) return <Badge tone="warn">Cooldown</Badge>;
    return <Badge tone="good">Active</Badge>;
}

function healthIsStale(account: Account) {
    if (!account.provider_health_checked_at) return true;
    return Date.now() - new Date(account.provider_health_checked_at).getTime() > 150_000;
}

function healthBadge(account: Account) {
    const health: ProviderHealth = account.provider_health;
    if (health === "REAUTH_REQUIRED") return <Badge tone="bad">Re-auth required</Badge>;
    return null;
}

function compareAccountsForDisplay(a: Account, b: Account): number {
    const aNeedsReauth = a.provider_health === "REAUTH_REQUIRED";
    const bNeedsReauth = b.provider_health === "REAUTH_REQUIRED";
    if (aNeedsReauth !== bNeedsReauth) return aNeedsReauth ? -1 : 1;
    return compareAccountsForRotation(a, b);
}

function accountIsUsable(account: Account, now = Date.now()): boolean {
    if (account.status === "DISABLED") return false;
    // The router intentionally permits DEGRADED/UNKNOWN accounts: a failed
    // quota probe must not remove an account whose normal Responses traffic
    // still works. Only re-authentication is a hard provider-health block.
    if (account.provider_health === "REAUTH_REQUIRED") return false;
    if (account.cooldown_until && new Date(account.cooldown_until).getTime() > now) return false;
    return (
        (account.five_hour_used_pct ?? 0) < account.five_hour_rotation_threshold &&
        (account.weekly_used_pct ?? 0) < account.weekly_rotation_threshold &&
        (account.monthly_used_pct ?? 0) < 1
    );
}

function accountQuotaWindows(account: Account) {
    return [
        {
            key: "five_hour",
            label: "5-hour window",
            fraction: account.five_hour_used_pct,
            resetAt: account.five_hour_reset_at,
        },
        {
            key: "weekly",
            label: "Weekly window",
            fraction: account.weekly_used_pct,
            resetAt: account.weekly_reset_at,
        },
        {
            key: "monthly",
            label: "Monthly window",
            fraction: account.monthly_used_pct,
            resetAt: account.monthly_reset_at,
        },
    ].filter((window) => window.fraction !== null || window.resetAt !== null);
}

function appearsAuthenticated(account: Account): boolean {
    return account.authenticated_override || account.provider_health !== "REAUTH_REQUIRED";
}

/**
 * Mirror backend account_selection_key for display ordering. Priority remains
 * the primary order; accounts sharing a priority are then ordered by the
 * earliest known weekly reset, followed by the earliest 5-hour reset. Unknown
 * reset times sort last, with creation time and id providing stable ties.
 */
function accountSelectionSortKey(value: string | null): [number, number] {
    if (!value) return [1, Number.POSITIVE_INFINITY];
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) ? [0, timestamp] : [1, Number.POSITIVE_INFINITY];
}

function compareAccountsForRotation(a: Account, b: Account): number {
    const priority = a.priority - b.priority;
    if (priority !== 0) return priority;

    for (const [aReset, bReset] of [
        [a.weekly_reset_at, b.weekly_reset_at],
        [a.five_hour_reset_at, b.five_hour_reset_at],
        [a.monthly_reset_at, b.monthly_reset_at],
    ] as const) {
        const [aKnown, aTimestamp] = accountSelectionSortKey(aReset);
        const [bKnown, bTimestamp] = accountSelectionSortKey(bReset);
        if (aKnown !== bKnown) return aKnown - bKnown;
        if (aTimestamp !== bTimestamp) return aTimestamp - bTimestamp;
    }

    const aCreated = Date.parse(a.created_at);
    const bCreated = Date.parse(b.created_at);
    if (Number.isFinite(aCreated) && Number.isFinite(bCreated) && aCreated !== bCreated) {
        return aCreated - bCreated;
    }
    return a.id.localeCompare(b.id);
}

function isTeamPlan(tier: string | null): boolean {
    const normalized = tier?.trim().toLocaleLowerCase();
    return normalized === "team" || normalized === "business";
}

function optionalNumber(value: string): number | null {
    if (!value.trim()) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function egressTargetLabel(account: Account, targets: EgressTarget[]): string {
    const target = targets.find((candidate) => candidate.id === account.egress_target_id);
    if (!target) return "First configured path";
    if (target.interface_name && target.private_ip) {
        return `${target.interface_name} · ${target.private_ip}${target.public_ip ? ` → ${target.public_ip}` : ""}`;
    }
    return target.label;
}

export default function AccountsPage() {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const queryString = searchParams.toString();
    const [accounts, setAccounts] = useState<Account[] | null>(null);
    const [egressTargets, setEgressTargets] = useState<EgressTarget[]>([]);
    const [savedPriorities, setSavedPriorities] = useState<Record<string, number>>({});
    const [draggedId, setDraggedId] = useState<string | null>(null);
    const [savingPriority, setSavingPriority] = useState(false);
    const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
    const [selectionMode, setSelectionMode] = useState(false);
    const [healthDetailsId, setHealthDetailsId] = useState<string | null>(null);
    const [bulkPriority, setBulkPriority] = useState("1");
    const [applyingBulkPriority, setApplyingBulkPriority] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);
    const [showAdd, setShowAdd] = useState(false);
    const [accountFilter, setAccountFilter] = useState<AccountFilter>("usable");
    const [accountSearch, setAccountSearch] = useState("");
    const [editTarget, setEditTarget] = useState<Account | null>(null);
    const [reauthTarget, setReauthTarget] = useState<Account | null>(null);
    const [resetTarget, setResetTarget] = useState<Account | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [, tick] = useState(0);
    const loadRequestId = useRef(0);

    const updateUrl = useCallback(
        (updates: Record<string, string | null | undefined>) => {
            const params = new URLSearchParams(queryString);
            Object.entries(updates).forEach(([key, value]) => {
                if (value === null || value === undefined || value === "") params.delete(key);
                else params.set(key, value);
            });
            const nextQueryString = params.toString();
            router.replace(nextQueryString ? `${pathname}?${nextQueryString}` : pathname, {
                scroll: false,
            });
        },
        [pathname, queryString, router],
    );

    useEffect(() => {
        const params = new URLSearchParams(queryString);
        const candidate = params.get("section");
        if (candidate === "all" || candidate === "authenticated" || candidate === "usable") {
            setAccountFilter(candidate);
        }
        setAccountSearch(params.get("search") ?? "");
    }, [queryString]);

    useEffect(() => {
        const timer = window.setInterval(() => tick((value) => value + 1), 1000);
        return () => window.clearInterval(timer);
    }, []);

    const load = useCallback(async (background = false) => {
        const requestId = ++loadRequestId.current;
        if (!background) setLoading(true);
        if (!background) setError(null);
        try {
            const [loaded, targets] = await Promise.all([api.accounts(), api.egressTargets()]);
            if (requestId !== loadRequestId.current) return;
            setAccounts(loaded);
            setEgressTargets(targets);
            setSavedPriorities(
                Object.fromEntries(loaded.map((account) => [account.id, account.priority])),
            );
        } catch (err) {
            if (requestId !== loadRequestId.current) return;
            setError(err instanceof Error ? err.message : "Failed to load accounts.");
        } finally {
            if (requestId === loadRequestId.current) setLoading(false);
        }
    }, []);

    const applyAccount = useCallback((updated: Account) => {
        setAccounts((current) => {
            if (!current) return [updated];
            return current.some((account) => account.id === updated.id)
                ? current.map((account) => (account.id === updated.id ? updated : account))
                : [...current, updated];
        });
        setSavedPriorities((current) => ({ ...current, [updated.id]: updated.priority }));
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    useEffect(() => {
        const refresh = () => {
            if (document.visibilityState === "visible") void load(true);
        };
        const timer = window.setInterval(refresh, 15_000);
        document.addEventListener("visibilitychange", refresh);
        return () => {
            window.clearInterval(timer);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [load]);

    const priorityDirty =
        accounts !== null &&
        accounts.some((account) => savedPriorities[account.id] !== account.priority);
    const toggleAccountSelection = (id: string) => {
        setSelectedAccountIds((current) =>
            current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
        );
    };
    const applyBulkPriority = async () => {
        const priority = Number(bulkPriority);
        if (
            !accounts ||
            selectedAccountIds.length === 0 ||
            !Number.isInteger(priority) ||
            priority < 1
        )
            return;
        setApplyingBulkPriority(true);
        setError(null);
        try {
            const updated = await api.bulkSetAccountPriority(selectedAccountIds, priority);
            setAccounts(updated);
            setSavedPriorities(
                Object.fromEntries(updated.map((account) => [account.id, account.priority])),
            );
            setSelectedAccountIds([]);
            setSelectionMode(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not set account priority.");
        } finally {
            setApplyingBulkPriority(false);
        }
    };
    const exitSelectionMode = () => {
        setSelectedAccountIds([]);
        setSelectionMode(false);
    };
    const toggleVisibleSelection = () => {
        if (selectedAccountIds.length === visibleAccounts.length) {
            setSelectedAccountIds([]);
            return;
        }
        setSelectedAccountIds(visibleAccounts.map((account) => account.id));
    };
    const normalizedSearch = accountSearch.trim().toLocaleLowerCase();
    const visibleAccounts =
        accounts
            ?.filter((account) => {
                if (accountFilter === "authenticated" && !appearsAuthenticated(account))
                    return false;
                if (accountFilter === "usable" && !accountIsUsable(account)) return false;
                if (!normalizedSearch) return true;
                return `${account.label}\n${account.account_email ?? ""}\n${account.workspace_name ?? ""}`
                    .toLocaleLowerCase()
                    .includes(normalizedSearch);
            })
            .sort(compareAccountsForDisplay) ?? [];
    const priorityLanes = Array.from(
        new Set(visibleAccounts.map((account) => account.priority)),
    ).sort((a, b) => a - b);

    const dropAccount = (priority: number) => {
        if (!accounts || !draggedId) {
            setDraggedId(null);
            return;
        }
        setAccounts(
            accounts.map((account) =>
                account.id === draggedId ? { ...account, priority } : account,
            ),
        );
        setDraggedId(null);
    };

    const savePriority = async () => {
        if (!accounts || !priorityDirty) return;
        setSavingPriority(true);
        setError(null);
        try {
            const changed = accounts.filter(
                (account) => savedPriorities[account.id] !== account.priority,
            );
            await Promise.all(
                changed.map((account) =>
                    api.updateAccount(account.id, { priority: account.priority }),
                ),
            );
            setSavedPriorities(
                Object.fromEntries(accounts.map((account) => [account.id, account.priority])),
            );
            void load(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save priority order.");
        } finally {
            setSavingPriority(false);
        }
    };

    const runAction = async (accountId: string, action: () => Promise<Account>) => {
        setBusyId(accountId);
        setError(null);
        try {
            applyAccount(await action());
            void load(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Account action failed.");
        } finally {
            setBusyId(null);
        }
    };

    const deleteAccount = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        try {
            await api.deleteAccount(deleteTarget.id);
            setAccounts(
                (current) =>
                    current?.filter((account) => account.id !== deleteTarget.id) ?? current,
            );
            setDeleteTarget(null);
            void load(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Delete failed.");
        } finally {
            setDeleting(false);
        }
    };

    return (
        <div className="space-y-6">
            <header>
                <div>
                    <h1 className="text-fog-100 font-serif text-2xl font-semibold tracking-tight">
                        Accounts
                    </h1>
                    <p className="text-fog-400 mt-1 max-w-2xl text-sm">
                        ChatGPT subscriptions pooled for Codex Responses API traffic. Drag an
                        account between priority lanes to change its priority; accounts within a
                        lane stay in deterministic rotation order.
                    </p>
                </div>
            </header>

            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="w-full md:w-72 md:shrink-0">
                    <TextInput
                        type="search"
                        value={accountSearch}
                        onChange={(event) => {
                            const next = event.target.value;
                            setAccountSearch(next);
                            updateUrl({ search: next });
                        }}
                        placeholder="Search name, email, or team…"
                        aria-label="Search accounts by name, email, or team"
                    />
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2 md:ml-auto">
                    <Segmented
                        options={ACCOUNT_FILTER_OPTIONS}
                        value={accountFilter}
                        onChange={(next) => {
                            setAccountFilter(next);
                            updateUrl({ section: next });
                        }}
                        label="Filter accounts"
                    />
                    <Button variant="ghost" onClick={() => void load(true)}>
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
                    <Button variant="primary" onClick={() => setShowAdd(true)}>
                        Add account
                    </Button>
                </div>
            </div>

            {selectionMode ? (
                <BulkPriorityBar
                    entityLabel="account"
                    selectedCount={selectedAccountIds.length}
                    visibleCount={visibleAccounts.length}
                    priority={bulkPriority}
                    onPriorityChange={setBulkPriority}
                    onSelectVisible={toggleVisibleSelection}
                    onApply={() => void applyBulkPriority()}
                    onCancel={() => setSelectedAccountIds([])}
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
                <Card className="p-5">
                    <LoadingState />
                </Card>
            ) : !accounts ? (
                <Card className="p-5">
                    <ErrorState
                        message={error ?? "Could not load accounts."}
                        onRetry={() => void load()}
                    />
                </Card>
            ) : accounts.length === 0 ? (
                <Card className="p-5">
                    <EmptyState message="No pooled accounts yet. Add one with Codex device login." />
                </Card>
            ) : visibleAccounts.length === 0 ? (
                <Card className="p-5">
                    <EmptyState
                        message={
                            normalizedSearch
                                ? "No accounts match this search and filter."
                                : accountFilter === "authenticated"
                                  ? "No authenticated accounts found. Choose All to inspect accounts that need re-authentication."
                                  : "No accounts are currently usable. Choose All to inspect every linked account."
                        }
                    />
                </Card>
            ) : (
                <div className="space-y-8">
                    {priorityLanes.map((priority) => {
                        const laneAccounts = visibleAccounts.filter(
                            (account) => account.priority === priority,
                        );
                        return (
                            <section
                                key={priority}
                                aria-labelledby={`priority-${priority}`}
                                onDragOver={(event) => event.preventDefault()}
                                onDrop={() => dropAccount(priority)}
                                className={`rounded-xl border p-4 transition-colors ${
                                    draggedId
                                        ? "border-brand-500/50 bg-brand-500/5"
                                        : "border-ink-700 bg-ink-950/20"
                                }`}
                            >
                                <div className="mb-4 flex items-center justify-between gap-3">
                                    <div>
                                        <h2
                                            id={`priority-${priority}`}
                                            className="text-fog-100 text-sm font-semibold tracking-[0.18em] uppercase"
                                        >
                                            Priority {priority}
                                        </h2>
                                        <p className="text-fog-500 mt-1 text-xs">
                                            {laneAccounts.length} account
                                            {laneAccounts.length === 1 ? "" : "s"} · drop here to
                                            assign
                                        </p>
                                    </div>
                                    {draggedId ? (
                                        <span className="text-brand-300 text-xs">
                                            Release to move
                                        </span>
                                    ) : null}
                                </div>
                                {laneAccounts.length === 0 ? (
                                    <div className="border-ink-700 text-fog-500 rounded-lg border border-dashed px-4 py-6 text-center text-xs">
                                        Empty priority lane · drop an account here
                                    </div>
                                ) : (
                                    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
                                        {laneAccounts.map((account) => {
                                            const busy = busyId === account.id;
                                            const disabled = account.status === "DISABLED";
                                            const reauthRequired =
                                                account.provider_health === "REAUTH_REQUIRED";
                                            const usable = accountIsUsable(account);
                                            const probeWarning =
                                                account.provider_health === "DEGRADED" ||
                                                account.provider_health === "UNKNOWN" ||
                                                healthIsStale(account);
                                            const assignedEgress = egressTargetLabel(
                                                account,
                                                egressTargets,
                                            );
                                            return (
                                                <div
                                                    key={account.id}
                                                    draggable
                                                    aria-label={`Drag ${account.label} to another priority lane`}
                                                    aria-grabbed={draggedId === account.id}
                                                    onDragStart={() => setDraggedId(account.id)}
                                                    onDragEnd={() => setDraggedId(null)}
                                                    onDragOver={(event) => event.preventDefault()}
                                                    onDrop={(event) => {
                                                        event.stopPropagation();
                                                        dropAccount(priority);
                                                    }}
                                                    className={
                                                        draggedId === account.id ? "opacity-60" : ""
                                                    }
                                                >
                                                    <Card
                                                        className={`cursor-grab overflow-hidden transition-shadow active:cursor-grabbing ${selectedAccountIds.includes(account.id) ? "border-brand-400/80 shadow-[0_0_0_2px_color-mix(in_srgb,var(--color-brand-500)_35%,transparent)]" : ""}`}
                                                    >
                                                        <div className="flex">
                                                            <div
                                                                aria-hidden="true"
                                                                className={`w-1 shrink-0 transition-colors ${!usable ? "bg-bad-500" : probeWarning ? "bg-warn-500" : "bg-good-500"}`}
                                                            />
                                                            <div className="min-w-0 flex-1 p-5">
                                                                <div className="flex items-start justify-between gap-3">
                                                                    <div className="min-w-0">
                                                                        <div className="flex flex-wrap items-center gap-2">
                                                                            <span className="text-fog-100 truncate text-sm font-semibold">
                                                                                {account.label}
                                                                            </span>
                                                                            {selectionMode ? (
                                                                                <button
                                                                                    type="button"
                                                                                    onClick={(
                                                                                        event,
                                                                                    ) => {
                                                                                        event.stopPropagation();
                                                                                        toggleAccountSelection(
                                                                                            account.id,
                                                                                        );
                                                                                    }}
                                                                                    aria-pressed={selectedAccountIds.includes(
                                                                                        account.id,
                                                                                    )}
                                                                                    aria-label={`Select ${account.label}`}
                                                                                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${selectedAccountIds.includes(account.id) ? "border-brand-400 bg-brand-500 text-ink-950" : "border-ink-600 bg-ink-900 hover:border-brand-400 text-transparent"}`}
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
                                                                            {statusBadge(account)}
                                                                            {healthBadge(account)}
                                                                            {account.authenticated_override ? (
                                                                                <Badge tone="warn">
                                                                                    Always
                                                                                    authenticated
                                                                                </Badge>
                                                                            ) : null}
                                                                            <Badge tone="neutral">
                                                                                Priority{" "}
                                                                                {account.priority}
                                                                            </Badge>
                                                                            {account.tier ? (
                                                                                <Badge tone="brand">
                                                                                    <span
                                                                                        className="max-w-48 truncate"
                                                                                        title={
                                                                                            isTeamPlan(
                                                                                                account.tier,
                                                                                            ) &&
                                                                                            account.workspace_name
                                                                                                ? `Team: ${account.workspace_name}`
                                                                                                : tierLabel(
                                                                                                      account.tier,
                                                                                                  )
                                                                                        }
                                                                                    >
                                                                                        {isTeamPlan(
                                                                                            account.tier,
                                                                                        ) &&
                                                                                        account.workspace_name
                                                                                            ? `Team: ${account.workspace_name}`
                                                                                            : tierLabel(
                                                                                                  account.tier,
                                                                                              )}
                                                                                    </span>
                                                                                </Badge>
                                                                            ) : null}
                                                                            {account.reset_credits_available >
                                                                            0 ? (
                                                                                <Badge tone="good">
                                                                                    {
                                                                                        account.reset_credits_available
                                                                                    }{" "}
                                                                                    reset
                                                                                    {account.reset_credits_available ===
                                                                                    1
                                                                                        ? ""
                                                                                        : "s"}
                                                                                </Badge>
                                                                            ) : null}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-1 truncate text-xs">
                                                                            {account.account_email ??
                                                                                "Email unavailable"}
                                                                        </div>
                                                                        <div className="text-fog-500 mt-1 truncate text-[11px]">
                                                                            Egress ·{" "}
                                                                            {assignedEgress}
                                                                        </div>
                                                                        {false &&
                                                                        account.provider_health ===
                                                                            "REAUTH_REQUIRED" ? (
                                                                            <div
                                                                                className={`mt-3 rounded-lg border px-3 py-2.5 ${account.provider_health === "REAUTH_REQUIRED" ? "border-bad-500/30 bg-bad-500/10" : "border-warn-500/30 bg-warn-500/10"}`}
                                                                            >
                                                                                <div className="flex items-start gap-2">
                                                                                    <AlertTriangle
                                                                                        size={15}
                                                                                        className={
                                                                                            account.provider_health ===
                                                                                            "REAUTH_REQUIRED"
                                                                                                ? "text-bad-500 mt-0.5 shrink-0"
                                                                                                : "text-warn-500 mt-0.5 shrink-0"
                                                                                        }
                                                                                        aria-hidden="true"
                                                                                    />
                                                                                    <div className="min-w-0 flex-1">
                                                                                        <div
                                                                                            className={`text-xs font-semibold ${account.provider_health === "REAUTH_REQUIRED" ? "text-bad-500" : "text-warn-500"}`}
                                                                                        >
                                                                                            {account.provider_health ===
                                                                                            "REAUTH_REQUIRED"
                                                                                                ? "Re-authentication required"
                                                                                                : "Provider health probe warning"}
                                                                                        </div>
                                                                                        <p className="text-fog-300 mt-0.5 text-[11px] leading-relaxed">
                                                                                            {account.provider_health_message ??
                                                                                                "The latest provider health check did not complete successfully."}
                                                                                        </p>
                                                                                    </div>
                                                                                    <button
                                                                                        type="button"
                                                                                        onClick={() =>
                                                                                            setHealthDetailsId(
                                                                                                healthDetailsId ===
                                                                                                    account.id
                                                                                                    ? null
                                                                                                    : account.id,
                                                                                            )
                                                                                        }
                                                                                        aria-expanded={
                                                                                            healthDetailsId ===
                                                                                            account.id
                                                                                        }
                                                                                        className="text-fog-300 hover:text-fog-100 shrink-0 text-[11px] font-medium underline decoration-dotted underline-offset-2"
                                                                                    >
                                                                                        {healthDetailsId ===
                                                                                        account.id
                                                                                            ? "Hide"
                                                                                            : "Details"}
                                                                                    </button>
                                                                                </div>
                                                                                {healthDetailsId ===
                                                                                account.id ? (
                                                                                    <dl className="border-ink-700/70 mt-2 grid grid-cols-2 gap-x-3 gap-y-2 border-t pt-2 text-[11px]">
                                                                                        <div>
                                                                                            <dt className="text-fog-500">
                                                                                                Reason
                                                                                            </dt>
                                                                                            <dd className="text-fog-200 mt-0.5 font-mono">
                                                                                                {account.provider_health_code ??
                                                                                                    "unknown"}
                                                                                            </dd>
                                                                                        </div>
                                                                                        <div>
                                                                                            <dt className="text-fog-500">
                                                                                                Failures
                                                                                            </dt>
                                                                                            <dd className="text-fog-200 mt-0.5 font-mono tabular-nums">
                                                                                                {
                                                                                                    account.provider_health_failure_count
                                                                                                }
                                                                                            </dd>
                                                                                        </div>
                                                                                        <div>
                                                                                            <dt className="text-fog-500">
                                                                                                Last
                                                                                                success
                                                                                            </dt>
                                                                                            <dd className="text-fog-200 mt-0.5">
                                                                                                {formatDateTime(
                                                                                                    account.provider_health_last_success_at,
                                                                                                )}
                                                                                            </dd>
                                                                                        </div>
                                                                                        <div>
                                                                                            <dt className="text-fog-500">
                                                                                                Checked
                                                                                            </dt>
                                                                                            <dd className="text-fog-200 mt-0.5">
                                                                                                {formatDateTime(
                                                                                                    account.provider_health_checked_at,
                                                                                                )}
                                                                                            </dd>
                                                                                        </div>
                                                                                    </dl>
                                                                                ) : null}
                                                                            </div>
                                                                        ) : null}
                                                                    </div>
                                                                    <div className="flex shrink-0 items-center gap-2">
                                                                        {busy ? <Spinner /> : null}
                                                                    </div>
                                                                </div>

                                                                <div
                                                                    className={`mt-5 ${disabled ? "opacity-50" : ""}`}
                                                                >
                                                                    <div
                                                                        className={`border-ink-700 bg-ink-900/45 grid gap-3 rounded-lg border p-3 ${accountQuotaWindows(account).length > 1 ? "grid-cols-[repeat(auto-fit,minmax(180px,1fr))]" : ""}`}
                                                                    >
                                                                        {accountQuotaWindows(
                                                                            account,
                                                                        ).length === 0 ? (
                                                                            <div className="text-fog-400 text-xs">
                                                                                No quota data
                                                                                returned by OpenAI.
                                                                            </div>
                                                                        ) : (
                                                                            accountQuotaWindows(
                                                                                account,
                                                                            ).map((window) => (
                                                                                <div
                                                                                    key={window.key}
                                                                                    className="bg-ink-950/35 rounded-md p-2.5"
                                                                                >
                                                                                    <UsageBar
                                                                                        label={
                                                                                            window.label
                                                                                        }
                                                                                        fraction={
                                                                                            window.fraction
                                                                                        }
                                                                                    />
                                                                                    <div className="text-fog-400 mt-1.5 text-[11px]">
                                                                                        {formatCountdown(
                                                                                            window.resetAt,
                                                                                        )}
                                                                                    </div>
                                                                                </div>
                                                                            ))
                                                                        )}
                                                                    </div>
                                                                </div>

                                                                <div className="border-ink-700 bg-ink-900/45 mt-3 grid grid-cols-2 overflow-hidden rounded-lg border">
                                                                    <div className="border-ink-700 border-r px-3 py-2.5">
                                                                        <div className="text-fog-100 font-mono text-sm font-semibold tabular-nums">
                                                                            {formatUsd(
                                                                                account.total_spend_usd,
                                                                            )}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-[10px] tracking-wider uppercase">
                                                                            All-time spend
                                                                        </div>
                                                                    </div>
                                                                    <div className="px-3 py-2.5">
                                                                        <div className="text-fog-100 font-mono text-sm font-semibold tabular-nums">
                                                                            {formatUsd(
                                                                                account.monthly_spend_usd,
                                                                            )}
                                                                        </div>
                                                                        <div className="text-fog-400 mt-0.5 text-[10px] tracking-wider uppercase">
                                                                            This month
                                                                        </div>
                                                                    </div>
                                                                </div>

                                                                {cooldownIsActive(account) ? (
                                                                    <div className="border-warn-500/30 bg-warn-500/10 text-warn-500 mt-3 rounded-md border px-2.5 py-1.5 text-[11px]">
                                                                        Temporarily cooling down ·{" "}
                                                                        {formatCountdown(
                                                                            account.cooldown_until,
                                                                        )}
                                                                    </div>
                                                                ) : null}

                                                                <div className="text-fog-400 mt-4 grid grid-cols-2 gap-3 text-[11px]">
                                                                    <div>
                                                                        Quota checked
                                                                        <div className="text-fog-300">
                                                                            {formatDateTime(
                                                                                account.quota_refreshed_at,
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                    <div>
                                                                        Last used
                                                                        <div className="text-fog-300">
                                                                            {formatDateTime(
                                                                                account.last_used_at,
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                    <div>
                                                                        Provider checked
                                                                        <div className="text-fog-300">
                                                                            {formatDateTime(
                                                                                account.provider_health_checked_at,
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                </div>

                                                                <div className="border-ink-700 mt-4 flex flex-wrap gap-2 border-t pt-4">
                                                                    <Button
                                                                        variant="ghost"
                                                                        disabled={busy}
                                                                        onClick={() =>
                                                                            setEditTarget(account)
                                                                        }
                                                                    >
                                                                        Edit
                                                                    </Button>
                                                                    <Button
                                                                        variant="ghost"
                                                                        disabled={
                                                                            busy || reauthRequired
                                                                        }
                                                                        onClick={() =>
                                                                            void runAction(
                                                                                account.id,
                                                                                () =>
                                                                                    api.refreshQuota(
                                                                                        account.id,
                                                                                    ),
                                                                            )
                                                                        }
                                                                    >
                                                                        Refresh limits
                                                                    </Button>
                                                                    <Button
                                                                        variant="ghost"
                                                                        disabled={
                                                                            busy ||
                                                                            disabled ||
                                                                            account.provider_health !==
                                                                                "HEALTHY" ||
                                                                            account.five_hour_used_pct ===
                                                                                null ||
                                                                            (account.five_hour_reset_at !==
                                                                                null &&
                                                                                new Date(
                                                                                    account.five_hour_reset_at,
                                                                                ).getTime() >
                                                                                    Date.now())
                                                                        }
                                                                        onClick={() =>
                                                                            void runAction(
                                                                                account.id,
                                                                                () =>
                                                                                    api.warmupAccount(
                                                                                        account.id,
                                                                                    ),
                                                                            )
                                                                        }
                                                                    >
                                                                        Warm Up
                                                                    </Button>
                                                                    <Button
                                                                        variant={
                                                                            reauthRequired
                                                                                ? "primary"
                                                                                : "ghost"
                                                                        }
                                                                        disabled={busy}
                                                                        onClick={() =>
                                                                            setReauthTarget(account)
                                                                        }
                                                                    >
                                                                        Re-authenticate
                                                                    </Button>
                                                                    <Button
                                                                        variant={
                                                                            account.reset_credits_available >
                                                                            0
                                                                                ? "primary"
                                                                                : "ghost"
                                                                        }
                                                                        disabled={
                                                                            busy || reauthRequired
                                                                        }
                                                                        onClick={() =>
                                                                            setResetTarget(account)
                                                                        }
                                                                    >
                                                                        Limit resets
                                                                    </Button>
                                                                    {disabled ? (
                                                                        <Button
                                                                            variant="primary"
                                                                            disabled={busy}
                                                                            onClick={() =>
                                                                                void runAction(
                                                                                    account.id,
                                                                                    () =>
                                                                                        api.enableAccount(
                                                                                            account.id,
                                                                                        ),
                                                                                )
                                                                            }
                                                                        >
                                                                            Enable
                                                                        </Button>
                                                                    ) : (
                                                                        <Button
                                                                            variant="subtle"
                                                                            disabled={busy}
                                                                            onClick={() =>
                                                                                void runAction(
                                                                                    account.id,
                                                                                    () =>
                                                                                        api.disableAccount(
                                                                                            account.id,
                                                                                        ),
                                                                                )
                                                                            }
                                                                        >
                                                                            Disable
                                                                        </Button>
                                                                    )}
                                                                    <Button
                                                                        variant="danger"
                                                                        disabled={busy}
                                                                        onClick={() =>
                                                                            setDeleteTarget(account)
                                                                        }
                                                                    >
                                                                        Delete
                                                                    </Button>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </Card>
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}
                            </section>
                        );
                    })}
                </div>
            )}

            {showAdd ? (
                <DeviceLoginModal
                    onClose={() => setShowAdd(false)}
                    onDone={(updated) => {
                        setShowAdd(false);
                        applyAccount(updated);
                        void load(true);
                    }}
                />
            ) : null}
            {reauthTarget ? (
                <DeviceLoginModal
                    account={reauthTarget}
                    onClose={() => setReauthTarget(null)}
                    onDone={(updated) => {
                        setReauthTarget(null);
                        applyAccount(updated);
                        void load(true);
                    }}
                />
            ) : null}
            {editTarget ? (
                <EditAccountModal
                    account={editTarget}
                    egressTargets={egressTargets}
                    onClose={() => setEditTarget(null)}
                    onDone={(updated) => {
                        setEditTarget(null);
                        applyAccount(updated);
                        void load(true);
                    }}
                />
            ) : null}
            {resetTarget ? (
                <LimitResetModal
                    account={resetTarget}
                    onClose={() => setResetTarget(null)}
                    onChanged={(updated) => {
                        applyAccount(updated);
                        void load(true);
                    }}
                />
            ) : null}
            {deleteTarget ? (
                <ConfirmDialog
                    title="Delete account"
                    message={`Delete “${deleteTarget.label}”? Stored OAuth credentials will be removed and usage history will be detached. This cannot be undone.`}
                    confirmLabel="Delete"
                    busy={deleting}
                    onConfirm={() => void deleteAccount()}
                    onCancel={() => setDeleteTarget(null)}
                />
            ) : null}
        </div>
    );
}

function DeviceLoginModal({
    account,
    onClose,
    onDone,
}: {
    account?: Account;
    onClose: () => void;
    onDone: (account: Account) => void;
}) {
    const [login, setLogin] = useState<OAuthStartResponse | null>(null);
    const [label, setLabel] = useState(account?.label ?? "");
    const [starting, setStarting] = useState(true);
    const [completing, setCompleting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const start = useCallback(async () => {
        setStarting(true);
        setError(null);
        try {
            setLogin(await api.oauthStart());
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not start Codex login.");
        } finally {
            setStarting(false);
        }
    }, []);

    useEffect(() => {
        void start();
    }, [start]);

    const complete = async () => {
        if (!login) return;
        setCompleting(true);
        setError(null);
        try {
            const updated = account
                ? await api.reauthAccount(account.id, login.flow_token)
                : await api.oauthComplete(label.trim(), login.flow_token);
            onDone(updated);
        } catch (err) {
            if (err instanceof ApiError && err.status === 425) {
                setError("Approval is still pending. Finish the browser step, then try again.");
            } else {
                setError(err instanceof Error ? err.message : "Codex login failed.");
            }
            setCompleting(false);
        }
    };

    return (
        <Modal
            title={account ? `Re-authenticate ${account.label}` : "Add Codex account"}
            onClose={onClose}
            widthClass="max-w-xl"
        >
            {starting ? (
                <LoadingState label="Starting secure device login…" />
            ) : !login ? (
                <ErrorState
                    message={error ?? "Could not start login."}
                    onRetry={() => void start()}
                />
            ) : (
                <div className="space-y-5">
                    <p className="text-fog-300 text-sm">
                        Open the official OpenAI device page, sign in to the account you want in
                        this pool, and enter this one-time code. This does not modify the Codex
                        login already active on this machine.
                    </p>
                    <div className="border-ink-700 bg-ink-900 rounded-lg border p-4 text-center">
                        <div className="text-fog-400 text-xs tracking-wider uppercase">
                            Device code
                        </div>
                        <div className="text-fog-100 mt-2 font-mono text-2xl font-semibold tracking-[0.2em]">
                            {login.user_code}
                        </div>
                        <button
                            type="button"
                            className="text-brand-300 mt-2 text-xs hover:underline"
                            onClick={() => void navigator.clipboard.writeText(login.user_code)}
                        >
                            Copy code
                        </button>
                    </div>
                    <a
                        href={login.verification_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="border-brand-400/40 bg-brand-500/10 text-brand-300 hover:bg-brand-500/20 inline-flex rounded-md border px-3 py-2 text-sm font-medium"
                    >
                        Open OpenAI device login ↗
                    </a>
                    {!account ? (
                        <Field
                            label="Account label"
                            hint="A private name used only in this console."
                        >
                            <TextInput
                                value={label}
                                onChange={(event) => setLabel(event.target.value)}
                                placeholder="e.g. personal-pro"
                                required
                            />
                        </Field>
                    ) : null}
                    {error ? (
                        <div className="border-bad-500/30 bg-bad-500/10 text-bad-500 rounded-md border px-3 py-2 text-sm">
                            {error}
                        </div>
                    ) : null}
                    <div className="flex justify-end gap-2">
                        <Button variant="ghost" onClick={onClose} disabled={completing}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() => void complete()}
                            disabled={completing || (!account && !label.trim())}
                        >
                            {completing ? <Spinner /> : null}I approved the login
                        </Button>
                    </div>
                </div>
            )}
        </Modal>
    );
}

function EditAccountModal({
    account,
    egressTargets,
    onClose,
    onDone,
}: {
    account: Account;
    egressTargets: EgressTarget[];
    onClose: () => void;
    onDone: (account: Account) => void;
}) {
    const [label, setLabel] = useState(account.label);
    const [workspaceName, setWorkspaceName] = useState(account.workspace_name ?? "");
    const [authenticatedOverride, setAuthenticatedOverride] = useState(
        account.authenticated_override,
    );
    const [fiveHourThreshold, setFiveHourThreshold] = useState(
        String(account.five_hour_rotation_threshold),
    );
    const [weeklyThreshold, setWeeklyThreshold] = useState(
        String(account.weekly_rotation_threshold),
    );
    const [cooldown, setCooldown] = useState(String(account.cooldown_seconds));
    const [priority, setPriority] = useState(String(account.priority));
    const defaultEgressTargetId = egressTargets.find((target) => target.enabled)?.id ?? "";
    const [egressTargetId, setEgressTargetId] = useState(
        account.egress_target_id ?? defaultEgressTargetId,
    );
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const save = async (event: React.FormEvent) => {
        event.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const updated = await api.updateAccount(account.id, {
                label: label.trim(),
                workspace_name: isTeamPlan(account.tier) ? workspaceName.trim() || null : undefined,
                authenticated_override: authenticatedOverride,
                five_hour_rotation_threshold: optionalNumber(fiveHourThreshold),
                weekly_rotation_threshold: optionalNumber(weeklyThreshold),
                cooldown_seconds: optionalNumber(cooldown),
                priority: optionalNumber(priority),
                egress_target_id: egressTargetId || defaultEgressTargetId || null,
            });
            onDone(updated);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Save failed.");
            setSaving(false);
        }
    };

    return (
        <Modal title={`Edit ${account.label}`} onClose={onClose}>
            <form onSubmit={save} className="space-y-4">
                <Field label="Label">
                    <TextInput
                        value={label}
                        onChange={(event) => setLabel(event.target.value)}
                        required
                    />
                </Field>
                <Field label="Email" hint="Managed by OAuth and updated on authentication.">
                    <div className="text-fog-200 text-sm">
                        {account.account_email ?? "Email unavailable"}
                    </div>
                </Field>
                <Field
                    label="Show in Authenticated accounts"
                    hint="Keeps this account in the Authenticated accounts view even when re-authentication is required. This does not authenticate the account or make it usable."
                >
                    <label className="border-fog-700 bg-ink-900 flex cursor-pointer items-center justify-between rounded-md border px-3 py-2">
                        <span className="text-fog-200 text-sm">
                            Include this account in the authenticated view
                        </span>
                        <input
                            type="checkbox"
                            checked={authenticatedOverride}
                            onChange={(event) => setAuthenticatedOverride(event.target.checked)}
                            className="accent-brand-500 h-4 w-4"
                        />
                    </label>
                </Field>
                {isTeamPlan(account.tier) ? (
                    <Field
                        label="Team / workspace name"
                        hint="Shown on every account linked to this ChatGPT Team workspace. Updating it here updates the other members too."
                    >
                        <TextInput
                            value={workspaceName}
                            onChange={(event) => setWorkspaceName(event.target.value)}
                            maxLength={200}
                            placeholder="e.g. Recallr AI"
                        />
                    </Field>
                ) : null}
                <Field
                    label="5-hour rotation threshold"
                    hint="Fraction from 0 to 1; 0.95 means 95%."
                >
                    <TextInput
                        value={fiveHourThreshold}
                        onChange={(event) => setFiveHourThreshold(event.target.value)}
                    />
                </Field>
                <Field
                    label="Weekly rotation threshold"
                    hint="Fraction from 0 to 1; 0.95 means 95%."
                >
                    <TextInput
                        value={weeklyThreshold}
                        onChange={(event) => setWeeklyThreshold(event.target.value)}
                    />
                </Field>
                <Field label="Cooldown seconds">
                    <TextInput
                        value={cooldown}
                        onChange={(event) => setCooldown(event.target.value)}
                    />
                </Field>
                <Field
                    label="Priority"
                    hint="1 is tried first. Moving this account automatically shifts the others."
                >
                    <TextInput
                        type="number"
                        min={1}
                        step={1}
                        value={priority}
                        onChange={(event) => setPriority(event.target.value)}
                    />
                </Field>
                <Field
                    label="Egress network path"
                    hint="Accounts use the first configured regional path by default. Select another path to pin this account to it."
                >
                    <SelectMenu
                        value={egressTargetId}
                        onChange={setEgressTargetId}
                        ariaLabel="Select egress network path"
                        options={[
                            ...egressTargets
                                .filter((target) => target.enabled)
                                .map((target) => ({
                                    value: target.id,
                                    label:
                                        target.interface_name && target.private_ip
                                            ? `${target.interface_name} · ${target.private_ip}${target.public_ip ? ` → ${target.public_ip}` : ""}`
                                            : target.label,
                                })),
                        ]}
                    />
                </Field>
                {error ? <div className="text-bad-500 text-sm">{error}</div> : null}
                <div className="flex justify-end gap-2">
                    <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
                        Cancel
                    </Button>
                    <Button type="submit" variant="primary" disabled={saving || !label.trim()}>
                        {saving ? <Spinner /> : null} Save
                    </Button>
                </div>
            </form>
        </Modal>
    );
}

function LimitResetModal({
    account,
    onClose,
    onChanged,
}: {
    account: Account;
    onClose: () => void;
    onChanged: (account: Account) => void;
}) {
    const [credits, setCredits] = useState<RateLimitResetCredit[] | null>(null);
    const [available, setAvailable] = useState(account.reset_credits_available);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            const response = await api.limitResets(account.id);
            setCredits(response.credits);
            setAvailable(response.available_count);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not load limit resets.");
        }
    }, [account.id]);

    useEffect(() => {
        void load();
    }, [load]);

    const consume = async (creditId?: string) => {
        setBusy(true);
        setError(null);
        setResult(null);
        try {
            const response = await api.consumeLimitReset(account.id, creditId);
            if (response.code === "reset") {
                setResult(
                    `Reset applied to ${response.windows_reset} limit window${response.windows_reset === 1 ? "" : "s"}.`,
                );
            } else if (response.code === "nothing_to_reset") {
                setResult("The account has no exhausted window to reset right now.");
            } else if (response.code === "already_redeemed") {
                setResult("This reset was already redeemed; no duplicate was consumed.");
            } else {
                setResult("No banked reset credit is available.");
            }
            await load();
            onChanged(response.account);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Limit reset failed.");
        } finally {
            setBusy(false);
        }
    };

    const availableCredits = credits?.filter((credit) => credit.status === "available") ?? [];
    const usageEligible =
        account.weekly_used_pct !== null && account.weekly_used_pct > LIMIT_RESET_MIN_WEEKLY_USAGE;
    const hasExpiringCredit = availableCredits.some(resetCreditExpiresSoon);

    return (
        <Modal title={`Limit resets · ${account.label}`} onClose={onClose} widthClass="max-w-xl">
            <div className="space-y-4">
                <div className="border-warn-500/30 bg-warn-500/10 text-fog-200 rounded-md border p-3 text-sm">
                    A banked reset is a real, expiring account entitlement. Redeeming one consumes
                    it upstream and resets any eligible Codex limit windows; it does not fabricate
                    local quota.
                </div>
                {!usageEligible ? (
                    <div className="border-ink-600 bg-ink-900 text-fog-300 rounded-md border p-3 text-sm">
                        {hasExpiringCredit
                            ? "Weekly usage is not above 90%, but resets expiring within 12 hours can be redeemed."
                            : "Weekly usage must be above 90%. Redemption is otherwise available only for resets expiring within 12 hours."}
                    </div>
                ) : null}
                <div className="flex items-center justify-between">
                    <span className="text-fog-300 text-sm">Available credits</span>
                    <Badge tone={available > 0 ? "good" : "neutral"}>{available}</Badge>
                </div>
                {credits === null && !error ? (
                    <LoadingState label="Loading reset credits…" />
                ) : null}
                {availableCredits.map((credit) => (
                    <div key={credit.id} className="border-ink-700 rounded-lg border p-3">
                        <div className="text-fog-100 text-sm font-medium">
                            {credit.title || "Codex rate-limit reset"}
                        </div>
                        {credit.description ? (
                            <p className="text-fog-400 mt-1 text-xs">{credit.description}</p>
                        ) : null}
                        <div className="text-fog-400 mt-2 flex items-center justify-between text-[11px]">
                            <span>Expires {formatDateTime(credit.expires_at)}</span>
                            <Button
                                variant="primary"
                                disabled={
                                    busy ||
                                    (!usageEligible && !resetCreditExpiresSoon(credit)) ||
                                    credit.is_supported_by_plan === false
                                }
                                onClick={() => void consume(credit.id)}
                            >
                                {busy ? <Spinner /> : null}{" "}
                                {credit.is_supported_by_plan === false
                                    ? "Not supported by this plan"
                                    : "Redeem this reset"}
                            </Button>
                        </div>
                    </div>
                ))}
                {credits && availableCredits.length === 0 ? (
                    <p className="text-fog-400 text-sm">
                        No individually selectable reset is available.
                    </p>
                ) : null}
                {available > 0 && availableCredits.length === 0 ? (
                    <Button
                        variant="primary"
                        disabled={busy || !usageEligible}
                        onClick={() => void consume()}
                    >
                        {busy ? <Spinner /> : null} Redeem available reset
                    </Button>
                ) : null}
                {result ? <div className="text-good-500 text-sm">{result}</div> : null}
                {error ? <div className="text-bad-500 text-sm">{error}</div> : null}
                <div className="flex justify-end">
                    <Button variant="ghost" onClick={onClose} disabled={busy}>
                        Close
                    </Button>
                </div>
            </div>
        </Modal>
    );
}
