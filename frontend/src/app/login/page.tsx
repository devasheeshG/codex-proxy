"use client";

// ---------------------------------------------------------------------------
// Login page. Posts credentials, stores the token, redirects to the overview.
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getToken, setToken } from "@/lib/api";
import { Button, Field, Spinner, TextInput } from "@/components/ui";
import { CodexLogo } from "@/components/CodexLogo";

export default function LoginPage() {
    const router = useRouter();

    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // If already authenticated, skip the login screen.
    useEffect(() => {
        if (getToken()) {
            router.replace("/");
        }
    }, [router]);

    const onSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setSubmitting(true);

        try {
            const res = await api.login(username, password);
            setToken(res.token);
            router.replace("/");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Login failed.");
            setSubmitting(false);
        }
    };

    return (
        <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
            <div
                aria-hidden
                className="bg-brand-500/10 pointer-events-none absolute top-[-18rem] h-[32rem] w-[32rem] rounded-full blur-[140px]"
            />
            <div className="w-full max-w-sm">
                {/* Brand */}
                <div className="mb-8 flex flex-col items-center text-center">
                    <div className="border-ink-600 bg-ink-850 mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border shadow-[var(--shadow-card)]">
                        <CodexLogo className="text-fog-100 h-9 w-9" title="OpenAI" />
                    </div>
                    <h1 className="text-fog-100 text-2xl font-semibold tracking-tight">
                        Codex Proxy
                    </h1>
                    <p className="text-fog-400 mt-1.5 text-sm">
                        Independent account gateway for OpenAI Codex.
                    </p>
                </div>

                <form
                    onSubmit={onSubmit}
                    className="border-ink-700 bg-ink-850 space-y-4 rounded-xl border p-6"
                >
                    <Field label="Username">
                        <TextInput
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            autoComplete="username"
                            autoFocus
                            required
                        />
                    </Field>

                    <Field label="Password">
                        <TextInput
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            autoComplete="current-password"
                            required
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

                    <Button
                        type="submit"
                        variant="primary"
                        className="w-full"
                        disabled={submitting}
                    >
                        {submitting ? <Spinner /> : null}
                        Sign in
                    </Button>
                </form>
                <p className="text-fog-400 mt-5 text-center text-[11px] leading-relaxed">
                    OpenAI and Codex are trademarks of OpenAI.
                </p>
            </div>
        </div>
    );
}
