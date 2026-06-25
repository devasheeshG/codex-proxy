"use client";

// ---------------------------------------------------------------------------
// Client-side auth gate. Redirects to /login when no token is present.
// Wraps the authenticated dashboard area.
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";
import { LoadingState } from "./ui";

export function AuthGuard({ children }: { children: React.ReactNode }) {
    const router = useRouter();
    const [checked, setChecked] = useState(false);

    useEffect(() => {
        if (!getToken()) {
            router.replace("/login");
            return;
        }
        setChecked(true);
    }, [router]);

    // Until we've confirmed a token client-side, render a spinner so we never
    // flash protected content.
    if (!checked) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <LoadingState label="Checking session…" />
            </div>
        );
    }

    return <>{children}</>;
}
