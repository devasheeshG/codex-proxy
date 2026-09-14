"use client";

import { useEffect, useRef, useState } from "react";
import { Check, UserRound } from "lucide-react";
import { UserLookup } from "@/lib/types";

export function UserFilter({
    users,
    value,
    onChange,
}: {
    users: UserLookup[];
    value: string | null;
    onChange: (id: string | null) => void;
}) {
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!open) return;
        const outside = (event: MouseEvent) => {
            if (!ref.current?.contains(event.target as Node)) setOpen(false);
        };
        const escape = (event: KeyboardEvent) => {
            if (event.key === "Escape") setOpen(false);
        };
        document.addEventListener("mousedown", outside);
        document.addEventListener("keydown", escape);
        return () => {
            document.removeEventListener("mousedown", outside);
            document.removeEventListener("keydown", escape);
        };
    }, [open]);
    const selected = users.find((user) => user.id === value);
    return (
        <div className="relative" ref={ref}>
            <button
                type="button"
                aria-haspopup="listbox"
                aria-expanded={open}
                onClick={() => setOpen((current) => !current)}
                className="border-ink-700 bg-ink-900 text-fog-100 hover:bg-ink-850 inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-medium"
            >
                <UserRound size={14} className="text-fog-400" />
                <span className="max-w-[14rem] truncate">{selected?.name ?? "All users"}</span>
            </button>
            {open ? (
                <div
                    role="listbox"
                    className="border-ink-700 bg-ink-850 fixed inset-x-3 top-3 z-50 max-h-[calc(100dvh-1.5rem)] min-w-0 overflow-y-auto rounded-xl border py-1 shadow-[var(--shadow-pop)] sm:absolute sm:inset-x-auto sm:top-full sm:right-0 sm:mt-2 sm:max-h-80 sm:min-w-[220px]"
                >
                    {[{ id: null, name: "All users" }, ...users].map((user) => {
                        const selectedOption = user.id === value;
                        return (
                            <button
                                key={user.id ?? "all"}
                                type="button"
                                role="option"
                                aria-selected={selectedOption}
                                onClick={() => {
                                    onChange(user.id);
                                    setOpen(false);
                                }}
                                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${selectedOption ? "bg-ink-700 text-fog-100 font-medium" : "text-fog-200 hover:bg-ink-800"}`}
                            >
                                {selectedOption ? (
                                    <Check size={14} className="text-brand-300 shrink-0" />
                                ) : (
                                    <span className="w-3.5 shrink-0" />
                                )}
                                <span className="min-w-0 break-words">{user.name}</span>
                            </button>
                        );
                    })}
                </div>
            ) : null}
        </div>
    );
}
