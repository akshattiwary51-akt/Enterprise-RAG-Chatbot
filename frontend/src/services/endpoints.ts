import { http } from "./api";
import type {
  AdminMetrics, AuditEvent, ChatMessage, Classification, ConversationSummary, DocumentRecord, Page, PassagePreview,
  Role, User,
} from "../types";

export const authApi = {
  login: (email: string, password: string) =>
    http.post<{ access_token: string; refresh_token: string; user: User }>("/auth/login", { email, password }).then((r) => r.data),
  logout: (refresh_token: string | null) => http.post("/auth/logout", { refresh_token }).then((r) => r.data),
  me: () => http.get<User>("/auth/me").then((r) => r.data),
  changePassword: (current_password: string, new_password: string) =>
    http.post("/auth/change-password", { current_password, new_password }).then((r) => r.data),
};

export interface AclInput {
  allowed_roles: string[]; user_grants: string[]; classification: Classification;
  department: string | null; owner_team: string | null;
}

export const documentsApi = {
  list: (page = 1, per_page = 50) => http.get<Page<DocumentRecord>>("/documents", { params: { page, per_page } }).then((r) => r.data),
  upload: (file: File, acl: { allowed_roles: string[]; user_grants: string[]; classification: Classification; owner_team?: string; department?: string }) => {
    const form = new FormData();
    form.append("file", file);
    acl.allowed_roles.forEach((r) => form.append("allowed_roles", r));
    acl.user_grants.forEach((u) => form.append("user_grants", u));
    form.append("classification", acl.classification);
    if (acl.owner_team) form.append("owner_team", acl.owner_team);
    if (acl.department) form.append("department", acl.department);
    return http.post<DocumentRecord>("/documents/upload", form).then((r) => r.data);
  },
  updatePermissions: (id: string, acl: AclInput) => http.patch<DocumentRecord>(`/documents/${id}/permissions`, acl).then((r) => r.data),
  reindex: (id: string) => http.post<DocumentRecord>(`/documents/${id}/reindex`, null).then((r) => r.data),
  remove: (id: string) => http.delete(`/documents/${id}`).then((r) => r.data),
  preview: (documentId: string, passageId: string) => http.get<PassagePreview>(`/documents/${documentId}/passages/${passageId}`).then((r) => r.data),
  download: async (id: string, filename: string) => {
    const res = await http.get(`/documents/${id}/download`, { responseType: "blob" });
    const url = URL.createObjectURL(res.data as Blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  },
};

export const chatApi = {
  send: (message: string, conversation_id: string | null) =>
    http.post<{ conversation_id: string; message: ChatMessage }>("/chat", { message, conversation_id }).then((r) => r.data),
  conversations: () => http.get<Page<ConversationSummary>>("/conversations", { params: { per_page: 100 } }).then((r) => r.data),
  conversation: (id: string) => http.get<{ id: string; title: string; messages: ChatMessage[] }>(`/conversations/${id}`).then((r) => r.data),
  remove: (id: string) => http.delete(`/conversations/${id}`).then((r) => r.data),
};

export const adminApi = {
  users: () => http.get<Page<User>>("/users", { params: { per_page: 100 } }).then((r) => r.data),
  roles: () => http.get<{ items: Role[] }>("/roles").then((r) => r.data.items),
  createUser: (u: { email: string; full_name: string; password: string; roles: string[]; department?: string | null }) =>
    http.post<User>("/users", u).then((r) => r.data),
  updateUser: (id: string, patch: Partial<{ full_name: string; is_active: boolean; department: string | null; password: string }>) =>
    http.patch<User>(`/users/${id}`, patch).then((r) => r.data),
  setRoles: (id: string, roles: string[]) => http.patch<User>(`/users/${id}/roles`, { roles }).then((r) => r.data),
  audit: (page: number, filters: { event_type?: string; result?: string }) =>
    http.get<Page<AuditEvent>>("/admin/audit-logs", { params: { page, per_page: 25, ...filters } }).then((r) => r.data),
  metrics: () => http.get<AdminMetrics>("/admin/metrics").then((r) => r.data),
};
