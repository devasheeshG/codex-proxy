"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DashboardMember } from "@/lib/types";
import {
    Button,
    Card,
    ErrorState,
    Field,
    LoadingState,
    SelectMenu,
    StatusToggle,
    TextInput,
} from "@/components/ui";

type Role = { id: string; label: string; permissions: string[] };

export default function TeamPage() {
    const [members, setMembers] = useState<DashboardMember[]>([]);
    const [roles, setRoles] = useState<Role[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [username, setUsername] = useState("");
    const [displayName, setDisplayName] = useState("");
    const [password, setPassword] = useState("");
    const [role, setRole] = useState("read_only");

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [memberResponse, roleResponse] = await Promise.all([
                api.teamMembers(),
                api.teamRoles(),
            ]);
            setMembers(memberResponse.members);
            setRoles(roleResponse.roles);
            setRole((current) =>
                roleResponse.roles.some((item) => item.id === current)
                    ? current
                    : (roleResponse.roles[0]?.id ?? "read_only"),
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Unable to load team members.");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const create = async (event: FormEvent) => {
        event.preventDefault();
        setSaving(true);
        setError(null);
        try {
            await api.createTeamMember({ username, display_name: displayName, password, role });
            setUsername("");
            setDisplayName("");
            setPassword("");
            await load();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Unable to create team member.");
        } finally {
            setSaving(false);
        }
    };

    const toggle = async (member: DashboardMember) => {
        try {
            const result = await api.updateTeamMember(member.id, { active: !member.active });
            setMembers((current) =>
                current.map((item) => (item.id === member.id ? result.member : item)),
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Unable to update team member.");
        }
    };

    const changeRole = async (member: DashboardMember, nextRole: string) => {
        try {
            const result = await api.updateTeamMember(member.id, { role: nextRole });
            setMembers((current) =>
                current.map((item) => (item.id === member.id ? result.member : item)),
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Unable to update member role.");
        }
    };

    const remove = async (member: DashboardMember) => {
        if (!window.confirm(`Delete ${member.display_name}?`)) return;
        try {
            await api.deleteTeamMember(member.id);
            setMembers((current) => current.filter((item) => item.id !== member.id));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Unable to delete team member.");
        }
    };

    return (
        <div className="space-y-5">
            <header>
                <h1 className="text-fog-100 font-serif text-2xl font-semibold tracking-tight">
                    Team access
                </h1>
                <p className="text-fog-400 mt-0.5 text-sm">
                    Manage dashboard members and their permission roles.
                </p>
            </header>
            {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
            <Card className="p-5">
                <h2 className="text-fog-100 text-base font-semibold">Add team member</h2>
                <form onSubmit={create} className="mt-4 grid gap-4 md:grid-cols-2">
                    <Field label="Username">
                        <TextInput
                            value={username}
                            onChange={(event) => setUsername(event.target.value)}
                            required
                        />
                    </Field>
                    <Field label="Display name">
                        <TextInput
                            value={displayName}
                            onChange={(event) => setDisplayName(event.target.value)}
                            required
                        />
                    </Field>
                    <Field label="Password">
                        <TextInput
                            type="password"
                            minLength={12}
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            required
                        />
                    </Field>
                    <Field label="Role">
                        <SelectMenu
                            value={role}
                            onChange={setRole}
                            options={roles.map((item) => ({ value: item.id, label: item.label }))}
                            ariaLabel="Role"
                        />
                    </Field>
                    <div className="md:col-span-2">
                        <Button type="submit" variant="primary" disabled={saving}>
                            {saving ? "Creating…" : "Create member"}
                        </Button>
                    </div>
                </form>
            </Card>
            {loading ? (
                <Card>
                    <LoadingState />
                </Card>
            ) : (
                <Card>
                    <div className="border-ink-700 text-fog-400 flex items-center justify-between border-b px-5 py-3 text-xs font-medium tracking-wider uppercase">
                        <span>Dashboard members</span>
                        <span>{members.length} members</span>
                    </div>
                    <div className="divide-ink-800 divide-y">
                        {members.length === 0 ? (
                            <div className="text-fog-400 p-5 text-sm">
                                No database-backed members yet.
                            </div>
                        ) : (
                            members.map((member) => (
                                <div
                                    key={member.id}
                                    className="flex flex-wrap items-center justify-between gap-4 px-5 py-4"
                                >
                                    <div>
                                        <div className="text-fog-100 font-medium">
                                            {member.display_name}
                                        </div>
                                        <div className="text-fog-400 text-xs">
                                            {member.username}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <SelectMenu
                                            value={member.role}
                                            onChange={(nextRole) =>
                                                void changeRole(member, nextRole)
                                            }
                                            options={roles.map((item) => ({
                                                value: item.id,
                                                label: item.label,
                                            }))}
                                            ariaLabel={`Role for ${member.display_name}`}
                                            className="w-48"
                                        />
                                        <StatusToggle
                                            on={member.active}
                                            onClick={() => void toggle(member)}
                                            srLabel={`Toggle ${member.display_name}`}
                                        />
                                    </div>
                                    <div className="text-fog-400 w-full text-xs md:w-auto md:max-w-md">
                                        {member.permissions
                                            .filter((item) => item !== "*")
                                            .join(" · ") || "Full owner access"}
                                    </div>
                                    <Button variant="danger" onClick={() => void remove(member)}>
                                        Delete
                                    </Button>
                                </div>
                            ))
                        )}
                    </div>
                </Card>
            )}
        </div>
    );
}
