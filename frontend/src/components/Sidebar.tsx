"use client";

// ---------------------------------------------------------------------------
// Navigation building blocks shared by the desktop sidebar and the mobile
// drawer (see AppShell): the brand mark, the nav links, and the sign-out button.
// ---------------------------------------------------------------------------

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Activity, BellRing, Boxes, Cable, LayoutDashboard, LogOut, Users } from "lucide-react";
import { clearToken } from "@/lib/api";
import { CodexLogo } from "./CodexLogo";

interface NavItem {
    href: string;
    label: string;
    icon: React.ReactNode;
}

const ICON = { size: 18, strokeWidth: 1.75 } as const;

const NAV: NavItem[] = [
    { href: "/", label: "Overview", icon: <LayoutDashboard {...ICON} /> },
    { href: "/accounts", label: "Accounts", icon: <Boxes {...ICON} /> },
    { href: "/fallbacks", label: "API fallbacks", icon: <Cable {...ICON} /> },
    { href: "/users", label: "Users", icon: <Users {...ICON} /> },
    { href: "/notifications", label: "Notifications", icon: <BellRing {...ICON} /> },
    { href: "/events", label: "Events", icon: <Activity {...ICON} /> },
];

export function BrandMark() {
    return (
        <div className="flex items-center gap-3">
            <div className="border-ink-600 bg-ink-800 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border">
                <CodexLogo className="text-fog-100 h-6 w-6" title="OpenAI" />
            </div>
            <div className="leading-tight">
                <div className="text-fog-100 text-[1.05rem] leading-tight font-semibold tracking-tight">
                    Codex Proxy
                </div>
                <div className="text-fog-400 text-[11px] font-medium tracking-[0.18em] uppercase">
                    Console
                </div>
            </div>
        </div>
    );
}

export function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
    const pathname = usePathname();
    const isActive = (href: string) =>
        href === "/" ? pathname === "/" : pathname.startsWith(href);

    return (
        <nav className="space-y-1">
            {NAV.map((item) => {
                const active = isActive(item.href);
                return (
                    <Link
                        key={item.href}
                        href={item.href}
                        onClick={onNavigate}
                        aria-current={active ? "page" : undefined}
                        className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                            active
                                ? "bg-brand-500/15 text-brand-300"
                                : "text-fog-300 hover:bg-ink-800 hover:text-fog-100"
                        }`}
                    >
                        <span className={active ? "text-brand-300" : "text-fog-400"}>
                            {item.icon}
                        </span>
                        {item.label}
                    </Link>
                );
            })}
        </nav>
    );
}

export function SignOutButton() {
    const router = useRouter();
    const onSignOut = () => {
        clearToken();
        router.replace("/login");
    };
    return (
        <button
            onClick={onSignOut}
            className="text-fog-300 hover:bg-ink-800 hover:text-bad-500 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
        >
            <LogOut size={18} strokeWidth={1.75} />
            Sign out
        </button>
    );
}

// Desktop sidebar — fixed rail, hidden below the md breakpoint (AppShell shows a
// top bar + drawer there instead).
export function Sidebar() {
    return (
        <aside className="border-ink-700 bg-ink-900 fixed inset-y-0 left-0 hidden w-60 flex-col border-r md:flex">
            <div className="px-5 py-5">
                <BrandMark />
            </div>
            <div className="flex-1 px-3 py-2">
                <NavLinks />
            </div>
            <div className="border-ink-700 border-t p-3">
                <SignOutButton />
            </div>
        </aside>
    );
}
