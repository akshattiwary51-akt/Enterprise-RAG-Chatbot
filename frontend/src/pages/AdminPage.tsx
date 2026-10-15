import { Activity, ClipboardList, Plus, ShieldAlert, Users as UsersIcon } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Card, EmptyState, ErrorBanner, Field, Modal, Spinner, SuccessBanner, inputCls } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { describeError } from "../services/api";
import { adminApi } from "../services/endpoints";
import type { AdminMetrics, AuditEvent, Page, Role, User } from "../types";
import { formatDate, titleCase } from "../utils/format";

type Tab = "overview" | "users" | "audit";

export default function AdminPage() {
  const { can } = useAuth();
  const canUsers = can("users.manage");
  const canAudit = can("audit.view");
  const tabs: { id: Tab; label: string; icon: typeof Activity; show: boolean }[] = [
    { id: "overview", label: "Overview", icon: Activity, show: canAudit },
    { id: "users", label: "Users", icon: UsersIcon, show: canUsers },
    { id: "audit", label: "Audit log", icon: ClipboardList, show: canAudit },
  ];
  const visible = tabs.filter((t) => t.show);
  const [tab, setTab] = useState<Tab>(visible[0]?.id ?? "users");

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
      <header><h1 className="text-xl font-semibold tracking-tight">Administration</h1>
        <p className="text-sm text-slate-500">Every action here is authorized and audited by the server.</p></header>
      <div role="tablist" className="flex gap-1 border-b border-slate-200">
        {visible.map(({ id, label, icon: Icon }) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium ${tab === id ? "border-brand-700 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
            <Icon className="h-4 w-4" aria-hidden="true" />{label}
          </button>))}
      </div>
      {tab === "overview" && canAudit && <Overview />}
      {tab === "users" && canUsers && <UsersTab />}
      {tab === "audit" && canAudit && <AuditTab />}
    </div>
  );
}

/* ------------------------------------------------------------------ overview */
function pct(v: number | null | undefined) { return v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(1)}%`; }

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function Overview() {
  const [m, setM] = useState<AdminMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { adminApi.metrics().then(setM).catch((e) => setError(describeError(e).message)); }, []);
  useEffect(load, [load]);

  if (error) return <ErrorBanner message={error} />;
  if (!m) return <div className="grid place-items-center py-16"><Spinner className="h-6 w-6 text-slate-400" /></div>;
  const failed = Object.entries(m.failed_access_last_24h);
  const c = m.runtime.counters;
  return (
    <div className="space-y-6">
      {m.model_config_problems.length > 0 && (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <p className="font-medium">AI model configuration needs attention</p>
          <ul className="list-disc pl-5">{m.model_config_problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>)}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Documents" value={Object.values(m.documents_by_status).reduce((a, b) => a + b, 0)} hint="all statuses" />
        <Stat label="Active users" value={`${m.users.active} / ${m.users.total}`} />
        <Stat label="No-answer rate" value={pct(m.runtime.derived.no_answer_rate)} hint="questions with no authorized evidence" />
        <Stat label="Citation coverage" value={pct(m.runtime.derived.citation_coverage)} hint="answers with a verified citation" />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Indexing health">
          {Object.keys(m.documents_by_status).length === 0 ? <p className="text-sm text-slate-500">No documents yet.</p> : (
            <ul className="space-y-2">{Object.entries(m.documents_by_status).map(([s, n]) => (
              <li key={s} className="flex items-center justify-between text-sm"><Badge value={s} /><span className="tabular-nums">{n}</span></li>))}</ul>)}
          <p className="mt-4 text-xs text-slate-500">Ingestion failure rate: {pct(m.runtime.derived.ingestion_failure_rate)}</p>
        </Card>
        <Card title="Failed and denied access (last 24 h)">
          {failed.length === 0 ? <EmptyState icon={<ShieldAlert className="h-8 w-8" />} title="Nothing to report" /> : (
            <ul className="space-y-2">{failed.map(([k, n]) => (
              <li key={k} className="flex items-center justify-between text-sm"><span>{k}</span><span className="font-medium tabular-nums">{n}</span></li>))}</ul>)}
        </Card>
      </div>
      <Card title="Retrieval and model activity" actions={<Button variant="ghost" onClick={load}>Refresh</Button>}>
        <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-slate-500">Questions answered</dt><dd className="font-medium tabular-nums">{c.rag_answers_total ?? 0}</dd></div>
          <div><dt className="text-slate-500">Authorized candidates</dt><dd className="font-medium tabular-nums">{c.retrieval_candidates_authorized_total ?? 0}</dd></div>
          <div><dt className="text-slate-500">Rejected candidates</dt><dd className="font-medium tabular-nums">{c.retrieval_candidates_rejected_total ?? 0}</dd></div>
          <div><dt className="text-slate-500">Auth failures</dt><dd className="font-medium tabular-nums">{c.auth_failures_total ?? 0}</dd></div>
          <div><dt className="text-slate-500">Model errors</dt><dd className="font-medium tabular-nums">{c.model_errors_total ?? 0}</dd></div>
          <div><dt className="text-slate-500">Tokens in / out</dt><dd className="font-medium tabular-nums">{c.llm_input_tokens_total ?? 0} / {c.llm_output_tokens_total ?? 0}</dd></div>
          <div><dt className="text-slate-500">Retrieval avg</dt><dd className="font-medium tabular-nums">{m.runtime.timers.retrieval_seconds?.avg_ms ?? 0} ms</dd></div>
          <div><dt className="text-slate-500">Ingestion avg</dt><dd className="font-medium tabular-nums">{m.runtime.timers.ingestion_seconds?.avg_ms ?? 0} ms</dd></div>
        </dl>
        <p className="mt-4 text-xs text-slate-500">Counters are per server process and reset on restart; export them to your monitoring stack for fleet-wide history.</p>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ users */
function UsersTab() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[] | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setUsers((await adminApi.users()).items); } catch (e) { setError(describeError(e).message); setUsers([]); }
  }, []);
  useEffect(() => { void load(); adminApi.roles().then(setRoles).catch(() => undefined); }, [load]);

  async function toggleActive(u: User) {
    setBusyId(u.id); setError(null); setNotice(null);
    try { await adminApi.updateUser(u.id, { is_active: !u.is_active }); setNotice(`${u.email} ${u.is_active ? "deactivated" : "reactivated"}. Their sessions end immediately.`); await load(); }
    catch (e) { setError(describeError(e).message); } finally { setBusyId(null); }
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <SuccessBanner message={notice} />
      <Card title="Users" actions={<Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New user</Button>}>
        {users === null ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6 text-slate-400" /></div> : (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500"><tr>
                <th className="px-5 py-2 font-medium">User</th><th className="px-3 py-2 font-medium">Roles</th><th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last sign-in</th><th className="px-5 py-2" /></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {users.map((u) => (
                  <tr key={u.id}>
                    <td className="px-5 py-3"><p className="font-medium">{u.full_name}{u.id === me?.id && <span className="ml-2 text-xs text-slate-400">(you)</span>}</p>
                      <p className="text-xs text-slate-500 break-all">{u.email}{u.department ? ` · ${u.department}` : ""}</p></td>
                    <td className="px-3 py-3"><div className="flex flex-wrap gap-1">{u.roles.map((r) => <Badge key={r} value="internal" label={r} />)}</div></td>
                    <td className="px-3 py-3">{u.is_active ? <Badge value="success" label="Active" /> : <Badge value="failure" label="Inactive" />}
                      {u.must_change_password && <span className="ml-1"><Badge value="denied" label="Must change password" /></span>}</td>
                    <td className="px-3 py-3 text-xs text-slate-500">{formatDate(u.last_login_at)}</td>
                    <td className="px-5 py-3"><div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => setEditing(u)}>Roles</Button>
                      <Button variant="ghost" onClick={() => setResetting(u)}>Reset password</Button>
                      <Button variant="ghost" busy={busyId === u.id} disabled={u.id === me?.id} onClick={() => void toggleActive(u)}>{u.is_active ? "Deactivate" : "Reactivate"}</Button>
                    </div></td>
                  </tr>))}
              </tbody>
            </table>
          </div>)}
      </Card>
      {creating && <CreateUserModal roles={roles} onClose={() => setCreating(false)} onDone={(m) => { setCreating(false); setNotice(m); void load(); }} />}
      {editing && <RolesModal user={editing} roles={roles} onClose={() => setEditing(null)} onDone={(m) => { setEditing(null); setNotice(m); void load(); }} />}
      {resetting && <ResetModal user={resetting} onClose={() => setResetting(null)} onDone={(m) => { setResetting(null); setNotice(m); void load(); }} />}
    </div>
  );
}

function RoleChecks({ roles, value, onChange }: { roles: Role[]; value: string[]; onChange: (v: string[]) => void }) {
  const toggle = (name: string) => onChange(value.includes(name) ? value.filter((r) => r !== name) : [...value, name]);
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {roles.map((r) => (
        <label key={r.name} className="flex items-start gap-2 rounded-lg border border-slate-200 p-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={value.includes(r.name)} onChange={() => toggle(r.name)} />
          <span><span className="font-medium">{titleCase(r.name)}</span><span className="block text-xs text-slate-500">{r.description}</span></span>
        </label>))}
    </div>
  );
}

function CreateUserModal({ roles, onClose, onDone }: { roles: Role[]; onClose: () => void; onDone: (msg: string) => void }) {
  const [email, setEmail] = useState(""); const [name, setName] = useState(""); const [password, setPassword] = useState("");
  const [department, setDepartment] = useState(""); const [sel, setSel] = useState<string[]>(["employee"]);
  const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { const u = await adminApi.createUser({ email: email.trim(), full_name: name.trim(), password, roles: sel, department: department.trim() || null });
      onDone(`${u.email} created. Share the initial password through a secure channel; they must change it at first sign-in.`); }
    catch (err) { setError(describeError(err).message); } finally { setBusy(false); }
  }
  return (
    <Modal title="New user" onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-4">
        <ErrorBanner message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Full name"><input className={inputCls} required value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Work email"><input className={inputCls} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Initial password" hint="12+ characters, mixed case, with a digit."><input className={inputCls} type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Field label="Department (optional)"><input className={inputCls} value={department} onChange={(e) => setDepartment(e.target.value)} /></Field>
        </div>
        <div><p className="mb-1 text-sm font-medium text-slate-700">Roles</p><RoleChecks roles={roles} value={sel} onChange={setSel} /></div>
        <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" busy={busy} disabled={sel.length === 0}>Create user</Button></div>
      </form>
    </Modal>
  );
}

function RolesModal({ user, roles, onClose, onDone }: { user: User; roles: Role[]; onClose: () => void; onDone: (msg: string) => void }) {
  const [sel, setSel] = useState<string[]>(user.roles); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true); setError(null);
    try { await adminApi.setRoles(user.id, sel); onDone(`Roles updated for ${user.email}. The change applies to their very next question.`); }
    catch (err) { setError(describeError(err).message); } finally { setBusy(false); }
  }
  return (
    <Modal title={`Roles · ${user.email}`} onClose={onClose} wide>
      <div className="space-y-4"><ErrorBanner message={error} /><RoleChecks roles={roles} value={sel} onChange={setSel} />
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} busy={busy} disabled={sel.length === 0}>Save roles</Button></div></div>
    </Modal>
  );
}

function ResetModal({ user, onClose, onDone }: { user: User; onClose: () => void; onDone: (msg: string) => void }) {
  const [password, setPassword] = useState(""); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { await adminApi.updateUser(user.id, { password }); onDone(`Password reset for ${user.email}. All of their sessions were ended and they must choose a new password.`); }
    catch (err) { setError(describeError(err).message); } finally { setBusy(false); }
  }
  return (
    <Modal title={`Reset password · ${user.email}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4"><ErrorBanner message={error} />
        <Field label="Temporary password" hint="12+ characters, mixed case, with a digit."><input className={inputCls} type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" busy={busy}>Reset password</Button></div></form>
    </Modal>
  );
}

/* ------------------------------------------------------------------ audit */
function AuditTab() {
  const [pageData, setPageData] = useState<Page<AuditEvent> | null>(null);
  const [page, setPage] = useState(1);
  const [eventType, setEventType] = useState("");
  const [result, setResult] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    adminApi.audit(page, { ...(eventType ? { event_type: eventType } : {}), ...(result ? { result } : {}) })
      .then(setPageData).catch((e) => setError(describeError(e).message));
  }, [page, eventType, result]);

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} />
      <Card title="Security audit events">
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Field label="Event type"><input className={inputCls} placeholder="e.g. document.access" value={eventType} onChange={(e) => { setPage(1); setEventType(e.target.value.trim()); }} /></Field>
          <Field label="Result"><select className={inputCls} value={result} onChange={(e) => { setPage(1); setResult(e.target.value); }}>
            <option value="">All</option><option value="success">Success</option><option value="failure">Failure</option><option value="denied">Denied</option></select></Field>
        </div>
        {pageData === null ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6 text-slate-400" /></div>
          : pageData.items.length === 0 ? <EmptyState icon={<ClipboardList className="h-10 w-10" />} title="No matching events" /> : (
            <div className="-mx-5 overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-500"><tr>
                  <th className="px-5 py-2 font-medium">When</th><th className="px-3 py-2 font-medium">Event</th><th className="px-3 py-2 font-medium">Result</th>
                  <th className="px-3 py-2 font-medium">Actor</th><th className="px-3 py-2 font-medium">Target</th><th className="px-5 py-2 font-medium">Details</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {pageData.items.map((e) => (
                    <tr key={e.id} className="align-top">
                      <td className="px-5 py-2 text-xs whitespace-nowrap text-slate-500">{formatDate(e.created_at)}</td>
                      <td className="px-3 py-2 font-medium">{e.event_type}</td>
                      <td className="px-3 py-2"><Badge value={e.result} /></td>
                      <td className="px-3 py-2 text-xs break-all">{e.actor ?? "—"}</td>
                      <td className="px-3 py-2 text-xs break-all text-slate-600">{e.target_type ? `${e.target_type}:${e.target_id ?? ""}` : "—"}</td>
                      <td className="px-5 py-2 text-xs text-slate-500"><code className="break-all">{Object.keys(e.details).length ? JSON.stringify(e.details) : "—"}</code>
                        {e.correlation_id && <p className="mt-0.5 text-[11px] text-slate-400">req {e.correlation_id.slice(0, 8)}</p>}</td>
                    </tr>))}
                </tbody>
              </table>
            </div>)}
        {pageData && pageData.pages > 1 && (
          <div className="mt-4 flex items-center justify-between text-sm">
            <span className="text-slate-500">Page {pageData.page} of {pageData.pages} · {pageData.total} events</span>
            <div className="flex gap-2"><Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <Button variant="secondary" disabled={page >= pageData.pages} onClick={() => setPage((p) => p + 1)}>Next</Button></div>
          </div>)}
      </Card>
    </div>
  );
}
