import { FileText, RefreshCw, ShieldCheck, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import AclEditor, { type AclState } from "../components/AclEditor";
import { Badge, Button, Card, EmptyState, ErrorBanner, Modal, Spinner, SuccessBanner } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { describeError } from "../services/api";
import { adminApi, documentsApi } from "../services/endpoints";
import type { DocumentRecord, Role, User } from "../types";
import { formatBytes, formatDate } from "../utils/format";

const ACCEPT = ".pdf,.docx,.xlsx,.xls,.txt,.md,.csv";
const EMPTY_ACL: AclState = { allowed_roles: [], user_grants: [], classification: "internal", department: "", owner_team: "" };

export default function DocumentsPage() {
  const { can } = useAuth();
  const canUpload = can("documents.upload");
  const isAdmin = can("documents.manage_permissions");
  const [docs, setDocs] = useState<DocumentRecord[] | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [acl, setAcl] = useState<AclState>(EMPTY_ACL);
  const [uploading, setUploading] = useState(false);
  const [drag, setDrag] = useState(false);
  const [editing, setEditing] = useState<DocumentRecord | null>(null);
  const [editAcl, setEditAcl] = useState<AclState>(EMPTY_ACL);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<DocumentRecord | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try { setDocs((await documentsApi.list()).items); } catch (e) { setError(describeError(e).message); setDocs([]); }
  }, []);
  useEffect(() => {
    void load();
    adminApi.roles().then(setRoles).catch(() => undefined);
    if (isAdmin) adminApi.users().then((p) => setUsers(p.items)).catch(() => undefined);
  }, [load, isAdmin]);
  useEffect(() => {
    if (!docs?.some((doc) => doc.status === "PENDING" || doc.status === "PROCESSING")) return;
    let timer: number;
    const poll = async () => {
      await load();
      timer = window.setTimeout(() => void poll(), 1500);
    };
    timer = window.setTimeout(() => void poll(), 1500);
    return () => window.clearTimeout(timer);
  }, [docs, load]);

  function pick(f: File | undefined | null) { if (f) { setFile(f); setError(null); setNotice(null); } }

  async function upload() {
    if (!file) return;
    setUploading(true); setError(null); setNotice(null);
    try {
      const doc = await documentsApi.upload(file, { ...acl, owner_team: acl.owner_team || undefined, department: acl.department || undefined });
      if (doc.status === "FAILED") setError(`"${doc.filename}" was uploaded but processing failed: ${doc.error_message ?? "unknown error"}`);
      else if (doc.status === "READY") setNotice(`"${doc.filename}" is indexed and ready (${doc.chunk_count} chunks).`);
      else setNotice(`"${doc.filename}" was uploaded and queued for indexing.`);
      setFile(null); setAcl(EMPTY_ACL); await load();
    } catch (e) { setError(describeError(e).message); } finally { setUploading(false); }
  }

  async function saveAcl() {
    if (!editing) return;
    setSaving(true);
    try {
      await documentsApi.updatePermissions(editing.id, { allowed_roles: editAcl.allowed_roles, user_grants: editAcl.user_grants, classification: editAcl.classification, department: editAcl.department || null, owner_team: editAcl.owner_team || null });
      setEditing(null); setNotice("Permissions updated. The change applies to the very next question."); await load();
    } catch (e) { setError(describeError(e).message); } finally { setSaving(false); }
  }

  async function run(id: string, fn: () => Promise<unknown>, done: string) {
    setRowBusy(id); setError(null);
    try { await fn(); setNotice(done); await load(); } catch (e) { setError(describeError(e).message); } finally { setRowBusy(null); }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      <header><h1 className="text-xl font-semibold tracking-tight">Documents</h1>
        <p className="text-sm text-slate-500">{isAdmin ? "Full inventory and indexing health." : "Documents you uploaded or are authorized to read."}</p></header>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <SuccessBanner message={notice} />

      {canUpload && (
        <Card title="Upload a document">
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
                onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
                className={`flex h-44 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed text-center ${drag ? "border-brand-600 bg-brand-50" : "border-slate-300 hover:border-slate-400"}`}>
                <UploadCloud className="h-8 w-8 text-slate-400" />
                {file ? <><p className="px-4 text-sm font-medium break-all">{file.name}</p><p className="text-xs text-slate-500">{formatBytes(file.size)}</p></>
                  : <><p className="text-sm font-medium">Drag a file here or click to browse</p><p className="text-xs text-slate-500">PDF, Word, Excel, CSV, TXT, Markdown</p></>}
                <input ref={input} type="file" accept={ACCEPT} className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
              </div>
            </div>
            <div className="space-y-4">
              <AclEditor value={acl} onChange={setAcl} roles={roles} users={users} isAdmin={isAdmin} />
              <Button onClick={() => void upload()} busy={uploading} disabled={!file || (acl.allowed_roles.length === 0 && acl.user_grants.length === 0)}>
                {uploading ? "Uploading…" : "Upload and queue"}
              </Button>
            </div>
          </div>
        </Card>
      )}

      <Card title="Document inventory" actions={<Button variant="ghost" onClick={() => void load()}><RefreshCw className="h-4 w-4" />Refresh</Button>}>
        {docs === null ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6 text-slate-400" /></div>
          : docs.length === 0 ? <EmptyState icon={<FileText className="h-10 w-10" />} title="No documents yet" hint={canUpload ? "Upload your first document above." : "Nothing has been shared with you yet."} />
          : (
            <div className="-mx-5 overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-500"><tr>
                  <th className="px-5 py-2 font-medium">Name</th><th className="px-3 py-2 font-medium">Status</th><th className="px-3 py-2 font-medium">Classification</th>
                  {isAdmin && <th className="px-3 py-2 font-medium">Roles</th>}<th className="px-3 py-2 font-medium">Updated</th><th className="px-5 py-2" /></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {docs.map((d) => (
                    <tr key={d.id} className="align-top">
                      <td className="px-5 py-3"><p className="font-medium break-all">{d.filename}</p><p className="text-xs text-slate-500">{d.file_type.toUpperCase()} · {formatBytes(d.size_bytes)} · {d.chunk_count} chunks</p>
                        {d.error_message && <p className="mt-1 max-w-xs text-xs text-red-600">{d.error_message}</p>}</td>
                      <td className="px-3 py-3"><Badge value={d.status} /></td>
                      <td className="px-3 py-3"><Badge value={d.classification} /></td>
                      {isAdmin && <td className="px-3 py-3 text-xs text-slate-600">{(d.allowed_roles ?? []).join(", ") || "—"}{(d.user_grants?.length ?? 0) > 0 && <span className="text-slate-400"> +{d.user_grants!.length} user(s)</span>}</td>}
                      <td className="px-3 py-3 text-xs text-slate-500">{formatDate(d.updated_at)}</td>
                      <td className="px-5 py-3"><div className="flex justify-end gap-1">
                        {isAdmin && <>
                          <Button variant="ghost" title="Edit permissions" aria-label={`Edit permissions for ${d.filename}`} onClick={() => { setEditing(d); setEditAcl({ allowed_roles: d.allowed_roles ?? [], user_grants: (d.user_grants ?? []).map((g) => g.id), classification: d.classification, department: d.department ?? "", owner_team: d.owner_team ?? "" }); }}><ShieldCheck className="h-4 w-4" /></Button>
                          <Button variant="ghost" title="Re-index" aria-label={`Re-index ${d.filename}`} busy={rowBusy === d.id} disabled={d.status === "PROCESSING"} onClick={() => void run(d.id, () => documentsApi.reindex(d.id), "Re-indexing queued.")}><RefreshCw className="h-4 w-4" /></Button>
                          <Button variant="ghost" title="Delete" aria-label={`Delete ${d.filename}`} onClick={() => setConfirmDelete(d)}><Trash2 className="h-4 w-4 text-red-500" /></Button></>}
                      </div></td>
                    </tr>))}
                </tbody>
              </table>
            </div>)}
      </Card>

      {editing && (
        <Modal title={`Permissions · ${editing.filename}`} onClose={() => setEditing(null)} wide>
          <div className="space-y-4">
            <AclEditor value={editAcl} onChange={setEditAcl} roles={roles} users={users} isAdmin={isAdmin} />
            <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button><Button onClick={() => void saveAcl()} busy={saving}>Save permissions</Button></div>
          </div>
        </Modal>)}
      {confirmDelete && (
        <Modal title="Delete document?" onClose={() => setConfirmDelete(null)}>
          <p className="text-sm text-slate-600">“{confirmDelete.filename}”, its search index and the stored file will be permanently removed. Past answers citing it will no longer be shown.</p>
          <div className="mt-4 flex justify-end gap-2"><Button variant="secondary" onClick={() => setConfirmDelete(null)}>Cancel</Button>
            <Button variant="danger" onClick={() => { const d = confirmDelete; setConfirmDelete(null); void run(d.id, () => documentsApi.remove(d.id), "Document deleted."); }}>Delete</Button></div>
        </Modal>)}
    </div>
  );
}
