// ---------------------------------------------------------------------------
// Shared API response types for the Codex Proxy admin dashboard.
// Every shape here mirrors a backend endpoint payload exactly.
// ---------------------------------------------------------------------------

// Auth -----------------------------------------------------------------------

export interface LoginResponse {
    token: string;
    token_type: string;
}

export interface AuthProfile {
    username: string;
    permissions: string[];
    root: boolean;
}

export interface DashboardMember {
    id: string;
    username: string;
    permissions: string[];
    active: boolean;
    created_at: string;
    last_login_at: string | null;
}

export interface UserLookup {
    id: string;
    name: string;
}

// Overview stats -------------------------------------------------------------

export interface OverviewStats {
    // Pool inventory — not range-dependent.
    total_accounts: number;
    active_accounts: number;
    usable_accounts: number;
    pool_used_pct: number;
    pool_remaining_pct: number;
    total_users: number;
    active_users: number;
    total_keys: number;
    // Usage over the selected range (defaults to month-to-date when no range is sent).
    tokens: number; // input + output only
    input_tokens: number;
    output_tokens: number;
    cached_input_tokens: number;
    input_output_ratio: number | null;
    cache_hit_rate: number;
    input_rate_pct: number;
    output_rate_pct: number;
    cache_hit_rate_pct: number;
    // API-equivalent value of the range's subscription usage (an ROI signal,
    // NOT money owed — subscriptions are flat-rate).
    api_equivalent_cost_usd: number;
    requests: number;
}

// Usage ----------------------------------------------------------------------

export interface UsageRecord {
    id: string;
    user_id: string;
    user_name: string | null;
    api_key_id: string | null;
    api_key_label: string | null;
    account_id: string | null;
    account_label: string | null;
    fallback_provider_id: string | null;
    fallback_provider_label: string | null;
    model: string;
    input_tokens: number;
    output_tokens: number;
    // Included in input_tokens; separated here for pricing and cache-hit visibility.
    cached_input_tokens: number;
    // Included in input_tokens when the upstream response reports a cache write.
    cache_write_tokens: number;
    // Reasoning effort requested for this individual response.
    reasoning_level: string | null;
    request_mode: RequestMode;
    // API-equivalent cost of this single request.
    cost_usd: number;
    status_code: number | null;
    created_at: string;
}

export interface UsagePage {
    total: number;
    limit: number;
    offset: number;
    items: UsageRecord[];
}

export interface ProxyEvent {
    id: string;
    created_at: string;
    request_id: string;
    user_id: string | null;
    api_key_id: string | null;
    account_id: string | null;
    fallback_provider_id: string | null;
    event_type: string;
    status_code: number | null;
    message: string | null;
    metadata: Record<string, unknown>;
    archive_hot?: boolean;
}

export interface ProxyEventPage {
    total: number;
    limit: number;
    offset: number;
    events: ProxyEvent[];
}

export interface ArchiveRequestDetail {
    event_id: string;
    request_body_path: string;
    response_body_path: string;
    request_available: boolean;
    response_available: boolean;
}

// Analytics ------------------------------------------------------------------

export interface ActivityPoint {
    ts: string; // ISO 8601 UTC bucket start
    requests: number;
    tokens: number;
}

export interface ActivityResponse {
    granularity: "hour" | "day";
    points: ActivityPoint[];
}

// A selected dashboard time window. Bounds are ISO 8601 UTC strings; an absent
// bound lets the backend apply its per-endpoint default.
export interface TimeRange {
    start?: string;
    end?: string;
    // Human label for the current selection, e.g. "Last 30 days" or a custom span.
    label: string;
}

export interface HourlyUserSlice {
    user_id: string;
    user_name: string;
    requests: number;
}

export interface HourlyUser {
    user_id: string;
    user_name: string;
}

export interface HourlyPoint {
    hour: number;
    requests: number;
    tokens: number;
    by_user: HourlyUserSlice[];
}

export interface HourlyResponse {
    points: HourlyPoint[];
    users: HourlyUser[];
}

export interface UserUsage {
    user_id: string;
    user_name: string | null;
    requests: number;
    tokens: number;
    input_tokens: number;
    output_tokens: number;
    last_used_at: string | null;
}

export interface ByUserResponse {
    users: UserUsage[];
}

// Model mix ------------------------------------------------------------------

export interface ModelSlice {
    model: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
}

export interface UserModelMix {
    user_id: string;
    user_name: string | null;
    total_requests: number;
    models: ModelSlice[];
}

export interface ModelMixResponse {
    users: UserModelMix[];
}

// Thinking-level mix ---------------------------------------------------------

export interface ThinkingLevelSlice {
    // Null means the client did not report an effort level for the request.
    thinking_level: string | null;
    requests: number;
    input_tokens: number;
    output_tokens: number;
}

export interface UserThinkingLevelMix {
    user_id: string;
    user_name: string | null;
    total_requests: number;
    thinking_levels: ThinkingLevelSlice[];
}

export interface ThinkingLevelMixResponse {
    users: UserThinkingLevelMix[];
}

// Distributions --------------------------------------------------------------

export interface PercentileBreakdown {
    min: number;
    p25: number;
    p50: number;
    p75: number;
    p90: number;
    p95: number;
    max: number;
}

export interface DistributionResponse {
    input_tokens: PercentileBreakdown;
    output_tokens: PercentileBreakdown;
    total_requests: number;
}

// Accounts -------------------------------------------------------------------

export type AccountStatus = "ACTIVE" | "DISABLED" | "COOLDOWN";
export type ProviderHealth = "UNKNOWN" | "HEALTHY" | "DEGRADED" | "REAUTH_REQUIRED";

export interface Account {
    id: string;
    label: string;
    account_email: string | null;
    tier: string | null;
    chatgpt_account_id: string | null;
    workspace_name: string | null;
    authenticated_override: boolean;
    warmup_enabled: boolean;
    warmup_next_at: string | null;
    warmup_last_at: string | null;
    warmup_last_status: string | null;
    warmup_last_error: string | null;
    status: AccountStatus;
    provider_health: ProviderHealth;
    provider_health_code: string | null;
    provider_health_message: string | null;
    provider_health_checked_at: string | null;
    provider_health_last_success_at: string | null;
    provider_health_failure_count: number;
    five_hour_used_pct: number | null;
    five_hour_reset_at: string | null;
    weekly_used_pct: number | null;
    weekly_reset_at: string | null;
    monthly_used_pct: number | null;
    monthly_reset_at: string | null;
    cooldown_until: string | null;
    reset_credits_available: number;
    quota_refreshed_at: string | null;
    last_used_at: string | null;
    created_at: string;
    // Per-window rotation policies (always set; tuned in the dashboard).
    five_hour_rotation_threshold: number;
    weekly_rotation_threshold: number;
    // Deprecated single-threshold alias.
    rotation_threshold: number;
    cooldown_seconds: number;
    max_failover_attempts: number;
    priority: number;
    egress_target_id: string | null;
    // API-equivalent value of traffic routed through this subscription account.
    total_spend_usd: number;
    monthly_spend_usd: number;
}

export interface EgressTarget {
    id: string;
    label: string;
    kind: string;
    interface_name: string | null;
    private_ip: string | null;
    public_ip: string | null;
    max_concurrency: number;
    enabled: boolean;
}

export interface OAuthStartResponse {
    verification_url: string;
    user_code: string;
    flow_token: string;
    interval: number;
}

export interface RateLimitResetCredit {
    id: string;
    reset_type: string;
    status: string;
    is_supported_by_plan: boolean | null;
    granted_at: string;
    expires_at: string | null;
    title: string | null;
    description: string | null;
}

export interface RateLimitResetCreditsResponse {
    available_count: number;
    credits: RateLimitResetCredit[];
}

export interface ConsumeLimitResetResponse {
    code: "reset" | "nothing_to_reset" | "no_credit" | "already_redeemed";
    windows_reset: number;
    idempotency_key: string;
    account: Account;
}

// OpenAI-compatible pay-as-you-go fallbacks ---------------------------------

export interface OpenAIFallback {
    id: string;
    label: string;
    base_url: string;
    key_hint: string;
    status: AccountStatus;
    provider_health: ProviderHealth;
    provider_health_message: string | null;
    provider_health_checked_at: string | null;
    cooldown_until: string | null;
    priority: number;
    monthly_spend_limit_usd: number | null;
    monthly_spend_usd: number;
    monthly_spend_remaining_usd: number | null;
    monthly_spend_reset_at: string;
    model_count: number;
    model_catalog_refreshed_at: string | null;
    last_used_at: string | null;
    created_at: string;
}

// Notifications ----------------------------------------------------------------

export interface TelegramSettings {
    enabled: boolean;
    configured: boolean;
    chat_id: string | null;
    message_thread_id: number | null;
    timezone: string;
    last_success_at: string | null;
    last_error_at: string | null;
    last_error: string | null;
}

export interface NotificationRule {
    event_type: string;
    title: string;
    description: string;
    enabled: boolean;
    template: string;
    default_template: string;
    cooldown_seconds: number;
    variables: string[];
}

export interface NotificationSettings {
    telegram: TelegramSettings;
    rules: NotificationRule[];
}

// Users + API keys -----------------------------------------------------------

export type RequestMode = "standard" | "fast" | "ultrafast";
export type ReasoningLevel = "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface User {
    id: string;
    name: string;
    active: boolean;
    priority: number;
    fallback_enabled: boolean;
    key_count: number;
    // requests/min across all the user's keys; null/0 = no user-level cap.
    rate_limit_per_minute: number | null;
    // tokens/calendar month across all the user's keys; null/0 = unlimited.
    monthly_token_budget: number | null;
    // One-time cap across the user's retained usage history.
    lifetime_token_budget: number | null;
    monthly_spend_budget_usd: number | null;
    lifetime_spend_budget_usd: number | null;
    allowed_request_modes: RequestMode[];
    allowed_reasoning_levels: ReasoningLevel[];
    // null allows every current and future model.
    allowed_models: string[] | null;
    // Client-facing model ID -> upstream model ID.
    model_overrides: Record<string, string>;
    last_used_at: string | null;
    created_at: string;
    total_tokens: number;
    total_requests: number;
    monthly_tokens_used: number;
    total_spend_usd: number;
    monthly_spend_usd: number;
    monthly_reset_at: string;
}

export interface ApiKey {
    id: string;
    user_id: string;
    label: string | null;
    key_prefix: string;
    active: boolean;
    // requests/min: null = use the server default; 0 = unlimited.
    rate_limit_per_minute: number | null;
    // tokens/calendar month: null/0 = unlimited.
    monthly_token_budget: number | null;
    last_used_at: string | null;
    created_at: string;
}

export interface ApiKeyCreated {
    api_key: ApiKey;
    // Plaintext secret — shown exactly once.
    secret: string;
}
