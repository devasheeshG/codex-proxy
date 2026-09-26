"use client";

import { useState } from "react";
import { Plus, SlidersHorizontal, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Preset, ReasoningLevel, RequestMode } from "@/lib/types";
import { Button, Field, Modal, SelectMenu, TextInput } from "@/components/ui";

const LEVELS: ReasoningLevel[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
const MODES: RequestMode[] = ["standard", "fast", "ultrafast"];

function toggle<T extends string>(values: T[], value: T): T[] {
    return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function Chips<T extends string>({
    values,
    selected,
    onChange,
}: {
    values: T[];
    selected: T[];
    onChange: (values: T[]) => void;
}) {
    return (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {values.map((value) => (
                <label
                    key={value}
                    className={`border-ink-700 flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs transition-colors ${selected.includes(value) ? "bg-brand-500/15 border-brand-500/60 text-brand-300" : "bg-ink-900 text-fog-400 hover:border-ink-500 hover:text-fog-100"}`}
                >
                    <input
                        type="checkbox"
                        checked={selected.includes(value)}
                        onChange={() => onChange(toggle(selected, value))}
                        className="accent-brand-500 h-4 w-4"
                    />
                    {value === "ultrafast" ? "UltraFast" : value}
                </label>
            ))}
        </div>
    );
}

export function ModelAccessEditor({
    models,
    value,
    onChange,
}: {
    models: string[];
    value: string[] | null;
    onChange: (value: string[] | null) => void;
}) {
    return (
        <div className="space-y-3">
            <label className="text-fog-200 flex items-center gap-2 text-sm">
                <input
                    type="checkbox"
                    checked={value === null}
                    onChange={(event) => onChange(event.target.checked ? null : models.slice(0, 1))}
                    className="accent-brand-500 h-4 w-4"
                />
                Allow every configured model
            </label>
            {value !== null && <Chips values={models} selected={value} onChange={onChange} />}
        </div>
    );
}

export function ModelRewritesEditor({
    models,
    value,
    onChange,
    targetModels = models,
}: {
    models: string[];
    value: Record<string, string>;
    onChange: (value: Record<string, string>) => void;
    targetModels?: string[];
}) {
    const [source, setSource] = useState("");
    const [target, setTarget] = useState("");
    return (
        <div className="space-y-2">
            {Object.entries(value).map(([from, to]) => (
                <div
                    key={from}
                    className="border-ink-700 flex items-center gap-2 rounded border p-2 text-xs"
                >
                    <code className="min-w-0 flex-1 break-all">
                        {from} → {to}
                    </code>
                    <button
                        type="button"
                        onClick={() => {
                            const next = { ...value };
                            delete next[from];
                            onChange(next);
                        }}
                        aria-label={`Remove ${from} rewrite`}
                        className="text-bad-500 hover:bg-bad-500/10 rounded p-1 transition-colors"
                    >
                        <Trash2 size={14} />
                    </button>
                </div>
            ))}
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                <SelectMenu
                    value={source}
                    onChange={setSource}
                    ariaLabel="Rewrite source"
                    options={[
                        { value: "", label: "Source model…" },
                        ...models.map((model) => ({ value: model, label: model })),
                    ]}
                />
                <SelectMenu
                    value={target}
                    onChange={setTarget}
                    ariaLabel="Rewrite target"
                    options={[
                        { value: "", label: "Target model…" },
                        ...targetModels.map((model) => ({ value: model, label: model })),
                    ]}
                />
                <Button
                    type="button"
                    variant="ghost"
                    disabled={!source || !target || source === target}
                    onClick={() => {
                        onChange({ ...value, [source]: target });
                        setSource("");
                        setTarget("");
                    }}
                >
                    Add
                </Button>
            </div>
        </div>
    );
}

export function ModelRulesEditor({
    models,
    levels,
    modes,
    onLevels,
    onModes,
    showLevels = true,
    showModes = true,
}: {
    models: string[];
    levels: Record<string, ReasoningLevel[]>;
    modes: Record<string, RequestMode[]>;
    onLevels: (value: Record<string, ReasoningLevel[]>) => void;
    onModes: (value: Record<string, RequestMode[]>) => void;
    showLevels?: boolean;
    showModes?: boolean;
}) {
    const [newModel, setNewModel] = useState("");
    const configured = [
        ...new Set([
            ...(showLevels ? Object.keys(levels) : []),
            ...(showModes ? Object.keys(modes) : []),
        ]),
    ];
    const remove = (model: string) => {
        const nextLevels = { ...levels };
        const nextModes = { ...modes };
        if (showLevels) delete nextLevels[model];
        if (showModes) delete nextModes[model];
        onLevels(nextLevels);
        onModes(nextModes);
    };
    return (
        <div className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row">
                <SelectMenu
                    value={newModel}
                    onChange={setNewModel}
                    ariaLabel="Model for a new per-model rule"
                    className="min-w-0 flex-1"
                    options={[
                        { value: "", label: "Choose a model…" },
                        ...models
                            .filter((model) => !configured.includes(model))
                            .map((model) => ({ value: model, label: model })),
                    ]}
                />
                <Button
                    type="button"
                    variant="ghost"
                    disabled={!newModel}
                    onClick={() => {
                        if (showLevels) onLevels({ ...levels, [newModel]: [...LEVELS] });
                        if (showModes) onModes({ ...modes, [newModel]: [...MODES] });
                        setNewModel("");
                    }}
                >
                    <Plus size={14} /> Add rule
                </Button>
            </div>
            {configured.map((model) => (
                <div
                    key={model}
                    className="border-ink-700 bg-ink-950/40 rounded-lg border p-3 sm:p-4"
                >
                    <div className="mb-3 flex items-center justify-between gap-2">
                        <code className="text-fog-100 min-w-0 text-sm break-all">{model}</code>
                        <button
                            type="button"
                            onClick={() => remove(model)}
                            aria-label={`Remove ${model} rule`}
                            className="text-bad-500 hover:bg-bad-500/10 rounded p-1 transition-colors"
                        >
                            <Trash2 size={15} />
                        </button>
                    </div>
                    <div className="space-y-3">
                        {showLevels && (
                            <Field label="Thinking levels">
                                <Chips
                                    values={LEVELS}
                                    selected={levels[model] ?? LEVELS}
                                    onChange={(items) => onLevels({ ...levels, [model]: items })}
                                />
                            </Field>
                        )}
                        {showModes && (
                            <Field label="Request modes">
                                <Chips
                                    values={MODES}
                                    selected={modes[model] ?? MODES}
                                    onChange={(items) => onModes({ ...modes, [model]: items })}
                                />
                            </Field>
                        )}
                    </div>
                </div>
            ))}
        </div>
    );
}

function PresetModal({
    preset,
    models,
    onClose,
    onSaved,
}: {
    preset: Preset | null;
    models: string[];
    onClose: () => void;
    onSaved: () => void;
}) {
    const [name, setName] = useState(preset?.name ?? "");
    const [allModels, setAllModels] = useState(preset?.allowed_models === null || !preset);
    const [allowedModels, setAllowedModels] = useState<string[]>(preset?.allowed_models ?? []);
    const [levels, setLevels] = useState<ReasoningLevel[]>(
        preset?.allowed_reasoning_levels ?? LEVELS,
    );
    const [modes, setModes] = useState<RequestMode[]>(preset?.allowed_request_modes ?? MODES);
    const [redirects, setRedirects] = useState<Record<string, string>>(
        preset?.model_overrides ?? {},
    );
    const [modelLevels, setModelLevels] = useState<Record<string, ReasoningLevel[]>>(
        preset?.model_reasoning_levels ?? {},
    );
    const [modelModes, setModelModes] = useState<Record<string, RequestMode[]>>(
        preset?.model_request_modes ?? {},
    );
    const [useGlobalThinking, setUseGlobalThinking] = useState(
        !preset || Object.keys(preset.model_reasoning_levels).length === 0,
    );
    const [previousGlobalLevels, setPreviousGlobalLevels] = useState<ReasoningLevel[]>(levels);
    const selectedModels = allModels ? models : allowedModels;
    const [source, setSource] = useState("");
    const [target, setTarget] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const save = async (event: React.FormEvent) => {
        event.preventDefault();
        setError(null);
        setBusy(true);
        try {
            const payload = {
                name: name.trim(),
                allowed_models: allModels ? null : allowedModels,
                allowed_reasoning_levels: levels,
                allowed_request_modes: modes,
                model_overrides: redirects,
                model_reasoning_levels: useGlobalThinking
                    ? {}
                    : Object.fromEntries(
                          selectedModels.map((model) => [
                              model,
                              modelLevels[model] ?? previousGlobalLevels,
                          ]),
                      ),
                model_request_modes: modelModes,
            };
            if (preset) await api.updatePreset(preset.id, payload);
            else await api.createPreset(payload);
            onSaved();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save preset");
        } finally {
            setBusy(false);
        }
    };
    return (
        <Modal
            title={preset ? `Edit ${preset.name}` : "Create preset"}
            onClose={onClose}
            widthClass="max-w-3xl"
        >
            <form onSubmit={(event) => void save(event)} className="space-y-5">
                <Field label="Preset name">
                    <TextInput
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        required
                        maxLength={120}
                        placeholder="e.g. Standard access"
                    />
                </Field>
                <section className="space-y-3">
                    <h3 className="text-fog-200 text-sm font-medium">Model access</h3>
                    <div className="space-y-3">
                        <label className="text-fog-200 flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={allModels}
                                onChange={(event) => setAllModels(event.target.checked)}
                            />
                            Allow every configured model
                        </label>
                        {!allModels && (
                            <div className="mt-3">
                                <Chips
                                    values={models}
                                    selected={allowedModels}
                                    onChange={setAllowedModels}
                                />
                            </div>
                        )}
                    </div>
                </section>
                <section className="space-y-3">
                    <h3 className="text-fog-200 text-sm font-medium">Global request policy</h3>
                    <div className="grid gap-5 md:grid-cols-2">
                        <Field label="Allowed thinking levels">
                            <label className="text-fog-200 mb-3 flex items-center gap-2 text-sm">
                                <input
                                    type="checkbox"
                                    className="accent-brand-500 h-4 w-4"
                                    checked={useGlobalThinking}
                                    onChange={(event) => {
                                        const enabled = event.target.checked;
                                        setUseGlobalThinking(enabled);
                                        if (enabled) {
                                            setLevels(previousGlobalLevels);
                                            setModelLevels({});
                                        } else {
                                            setPreviousGlobalLevels(levels);
                                            setLevels([...LEVELS]);
                                            setModelLevels(
                                                Object.fromEntries(
                                                    selectedModels.map((model) => [
                                                        model,
                                                        [...levels],
                                                    ]),
                                                ),
                                            );
                                        }
                                    }}
                                />
                                Same thinking levels for every selected model
                            </label>
                            {useGlobalThinking ? (
                                <Chips values={LEVELS} selected={levels} onChange={setLevels} />
                            ) : (
                                <p className="text-fog-400 text-xs">
                                    Choose thinking levels separately for each model below.
                                </p>
                            )}
                        </Field>
                        <Field label="Allowed request modes">
                            <Chips values={MODES} selected={modes} onChange={setModes} />
                        </Field>
                    </div>
                </section>
                {!useGlobalThinking && (
                    <section className="space-y-3">
                        <h3 className="text-fog-200 text-sm font-medium">
                            Thinking levels by model
                        </h3>
                        <div className="grid gap-3 sm:grid-cols-2">
                            {selectedModels.map((model) => (
                                <div
                                    key={model}
                                    className="border-ink-700 bg-ink-900/40 min-w-0 rounded-md border p-3"
                                >
                                    <code className="text-fog-100 mb-3 block text-xs break-all">
                                        {model}
                                    </code>
                                    <Chips
                                        values={LEVELS}
                                        selected={modelLevels[model] ?? previousGlobalLevels}
                                        onChange={(items) =>
                                            setModelLevels({ ...modelLevels, [model]: items })
                                        }
                                    />
                                </div>
                            ))}
                        </div>
                    </section>
                )}
                <section className="space-y-3">
                    <h3 className="text-fog-200 text-sm font-medium">Model rewrites</h3>
                    <div className="space-y-2">
                        <p className="text-fog-400 text-xs">
                            Use exact model IDs. The client-facing source must also be allowed
                            above.
                        </p>
                        {Object.entries(redirects).map(([from, to]) => (
                            <div
                                key={from}
                                className="border-ink-700 flex items-center gap-2 rounded border p-2 text-xs"
                            >
                                <code className="min-w-0 flex-1 break-all">
                                    {from} → {to}
                                </code>
                                <button
                                    type="button"
                                    onClick={() => {
                                        const next = { ...redirects };
                                        delete next[from];
                                        setRedirects(next);
                                    }}
                                    aria-label={`Remove ${from} rewrite`}
                                    className="text-bad-500 hover:bg-bad-500/10 rounded p-1 transition-colors"
                                >
                                    <Trash2 size={14} />
                                </button>
                            </div>
                        ))}
                        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                            <SelectMenu
                                value={source}
                                onChange={setSource}
                                ariaLabel="Rewrite source"
                                options={[
                                    { value: "", label: "Source model…" },
                                    ...models.map((model) => ({ value: model, label: model })),
                                ]}
                            />
                            <SelectMenu
                                value={target}
                                onChange={setTarget}
                                ariaLabel="Rewrite target"
                                options={[
                                    { value: "", label: "Target model…" },
                                    ...models
                                        .filter((model) => !model.startsWith("gpt-5.6-"))
                                        .map((model) => ({ value: model, label: model })),
                                ]}
                            />
                            <Button
                                type="button"
                                variant="ghost"
                                disabled={!source || !target || source === target}
                                onClick={() => {
                                    setRedirects({ ...redirects, [source]: target });
                                    setSource("");
                                    setTarget("");
                                }}
                            >
                                Add
                            </Button>
                        </div>
                    </div>
                </section>
                <Field
                    label="Per-model thinking and mode rules"
                    hint="Optional narrower limits for each model; requests must satisfy both the global and model rule."
                >
                    <ModelRulesEditor
                        models={models}
                        levels={modelLevels}
                        modes={modelModes}
                        onLevels={setModelLevels}
                        onModes={setModelModes}
                        showLevels={false}
                    />
                </Field>
                {error && (
                    <p role="alert" className="text-bad-500 text-sm">
                        {error}
                    </p>
                )}
                <div className="flex flex-wrap justify-end gap-2">
                    <Button type="button" variant="ghost" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        variant="primary"
                        disabled={
                            busy ||
                            !name.trim() ||
                            !levels.length ||
                            !modes.length ||
                            (!allModels && !allowedModels.length) ||
                            Object.values(modelLevels).some((items) => !items.length) ||
                            Object.values(modelModes).some((items) => !items.length)
                        }
                    >
                        {busy ? "Saving…" : "Save preset"}
                    </Button>
                </div>
            </form>
        </Modal>
    );
}

export function PresetsPanel({
    presets,
    models,
    onChanged,
}: {
    presets: Preset[];
    models: string[];
    onChanged: () => void;
}) {
    const [editing, setEditing] = useState<Preset | null>(null);
    const [creating, setCreating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const remove = async (preset: Preset) => {
        if (!window.confirm(`Delete preset “${preset.name}”?`)) return;
        try {
            await api.deletePreset(preset.id);
            onChanged();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not delete preset");
        }
    };
    return (
        <section
            className="border-ink-700 bg-ink-900 rounded-xl border p-4 sm:p-5"
            aria-label="Policy presets"
        >
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-fog-100 flex items-center gap-2 text-lg font-semibold">
                        <SlidersHorizontal size={18} className="text-brand-400" /> Policy presets
                    </h2>
                    <p className="text-fog-400 mt-1 text-xs">
                        Reusable model and thinking baselines. User overrides stay in place when a
                        preset changes.
                    </p>
                </div>
                <Button variant="primary" onClick={() => setCreating(true)}>
                    <Plus size={14} /> New preset
                </Button>
            </div>
            {error && (
                <p role="alert" className="text-bad-500 mt-3 text-sm">
                    {error}
                </p>
            )}
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {presets.map((preset) => (
                    <article
                        key={preset.id}
                        className="border-ink-700 bg-ink-950/40 rounded-lg border p-3.5"
                    >
                        <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                                <h3 className="text-fog-100 truncate text-sm font-semibold">
                                    {preset.name}
                                </h3>
                                <p className="text-fog-400 mt-0.5 text-xs">
                                    {preset.user_count} users assigned
                                </p>
                            </div>
                            <div className="flex shrink-0 gap-1">
                                <button
                                    type="button"
                                    aria-label={`Edit ${preset.name}`}
                                    onClick={() => setEditing(preset)}
                                    className="text-fog-400 hover:text-brand-300 rounded p-1.5"
                                >
                                    <SlidersHorizontal size={15} />
                                </button>
                                <button
                                    type="button"
                                    aria-label={`Delete ${preset.name}`}
                                    disabled={preset.user_count > 0}
                                    title={
                                        preset.user_count > 0
                                            ? "Reassign users first"
                                            : "Delete preset"
                                    }
                                    onClick={() => void remove(preset)}
                                    className="text-bad-500 hover:bg-bad-500/10 rounded p-1.5 transition-colors disabled:opacity-70"
                                >
                                    <Trash2 size={15} />
                                </button>
                            </div>
                        </div>
                    </article>
                ))}
            </div>
            {creating && (
                <PresetModal
                    preset={null}
                    models={models}
                    onClose={() => setCreating(false)}
                    onSaved={() => {
                        setCreating(false);
                        onChanged();
                    }}
                />
            )}
            {editing && (
                <PresetModal
                    preset={editing}
                    models={models}
                    onClose={() => setEditing(null)}
                    onSaved={() => {
                        setEditing(null);
                        onChanged();
                    }}
                />
            )}
        </section>
    );
}
