"use client";

// ---------------------------------------------------------------------------
// Shell for all authenticated pages: auth guard + responsive sidebar/drawer.
// ---------------------------------------------------------------------------

import { AuthGuard } from "@/components/AuthGuard";
import { AppShell } from "@/components/AppShell";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
    return (
        <AuthGuard>
            <AppShell>{children}</AppShell>
        </AuthGuard>
    );
}
