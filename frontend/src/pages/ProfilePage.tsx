import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Badge, Button, Card, ErrorBanner, Field, inputCls } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { describeError } from "../services/api";
import { authApi } from "../services/endpoints";
import { formatDate } from "../utils/format";

export default function ProfilePage() {
  const { user, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!user) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next !== confirm) { setError("The new passwords do not match."); return; }
    setBusy(true);
    try {
      await authApi.changePassword(current, next);
      await logout(); // the server revoked every session; sign in again with the new password
    } catch (err) { setError(describeError(err).message); } finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <header><h1 className="text-xl font-semibold tracking-tight">Profile</h1></header>
      {user.must_change_password && (
        <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Your password was set by an administrator. Please choose a new one to continue.
        </div>
      )}
      <Card title="Account">
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          <div><dt className="text-slate-500">Name</dt><dd className="font-medium">{user.full_name}</dd></div>
          <div><dt className="text-slate-500">Email</dt><dd className="font-medium break-all">{user.email}</dd></div>
          <div><dt className="text-slate-500">Department</dt><dd className="font-medium">{user.department ?? "—"}</dd></div>
          <div><dt className="text-slate-500">Last sign-in</dt><dd className="font-medium">{formatDate(user.last_login_at)}</dd></div>
          <div className="sm:col-span-2"><dt className="mb-1 text-slate-500">Roles</dt>
            <dd className="flex flex-wrap gap-1.5">{user.roles.map((r) => <Badge key={r} value="internal" label={r} />)}</dd></div>
          <div className="sm:col-span-2"><dt className="mb-1 text-slate-500">Capabilities</dt>
            <dd className="flex flex-wrap gap-1.5">{(user.permissions ?? []).length === 0
              ? <span className="text-slate-500">Ask questions about documents you are authorized to read.</span>
              : user.permissions!.map((p) => <Badge key={p} value="public" label={p} />)}</dd></div>
        </dl>
        <p className="mt-4 text-xs text-slate-500">Which documents you can search is decided by the server from your current roles and each document's access list.</p>
      </Card>

      <Card title="Change password">
        <form onSubmit={submit} className="max-w-sm space-y-4">
          <ErrorBanner message={error} />
          <Field label="Current password"><input className={inputCls} type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
          <Field label="New password" hint="At least 12 characters, with upper and lower case letters and a digit.">
            <input className={inputCls} type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} /></Field>
          <Field label="Confirm new password"><input className={inputCls} type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
          <Button type="submit" busy={busy}><KeyRound className="h-4 w-4" />Change password</Button>
          <p className="text-xs text-slate-500">You will be signed out everywhere and asked to sign in again.</p>
        </form>
      </Card>
    </div>
  );
}
