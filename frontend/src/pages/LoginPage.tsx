import { Lock, MessageSquare } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Button, ErrorBanner, Field, inputCls } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { describeError } from "../services/api";

export default function LoginPage() {
  const { user, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await login(email.trim(), password); }
    catch (err) {
      const info = describeError(err);
      setError(info.status === 429 ? "Too many attempts. Please wait a minute and try again." : info.message);
    } finally { setBusy(false); }
  }

  return (
    <div className="grid min-h-full place-items-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <div className="grid h-12 w-12 place-items-center rounded-xl bg-brand-700 text-white"><MessageSquare className="h-6 w-6" /></div>
          <h1 className="text-xl font-semibold tracking-tight">Knowledge Assistant</h1>
          <p className="text-sm text-slate-500">Sign in to search the company documents you are authorized to access.</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <ErrorBanner message={error} />
          <Field label="Work email"><input className={inputCls} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Password"><input className={inputCls} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Button type="submit" busy={busy} className="w-full"><Lock className="h-4 w-4" />Sign in</Button>
        </form>
        <p className="mt-4 text-center text-xs text-slate-500">Accounts are created by an administrator. There is no self-registration.</p>
      </div>
    </div>
  );
}
