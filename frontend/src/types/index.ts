export type Classification = "public" | "internal" | "confidential" | "highly_confidential";
export const CLASSIFICATIONS: Classification[] = ["public", "internal", "confidential", "highly_confidential"];

export interface User {
  id: string;
  email: string;
  full_name: string;
  roles: string[];
  department: string | null;
  is_active: boolean;
  must_change_password: boolean;
  created_at: string | null;
  last_login_at: string | null;
  permissions?: string[];
}

export interface Role { name: string; description: string }

export interface DocumentRecord {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  classification: Classification;
  owner_team: string | null;
  department: string | null;
  status: "PENDING" | "PROCESSING" | "READY" | "FAILED" | "DELETING";
  error_message: string | null;
  chunk_count: number;
  uploaded_by: string | null;
  created_at: string;
  updated_at: string;
  allowed_roles?: string[];
  user_grants?: { id: string; email: string }[];
}

export interface Citation {
  id: string;
  document_id: string;
  filename: string;
  passage_id: string | null;
  location: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  no_answer: boolean;
  redacted?: boolean;
  created_at: string;
}

export interface ConversationSummary { id: string; title: string; updated_at: string }

export interface Page<T> { items: T[]; page: number; per_page: number; total: number; pages: number }

export interface AuditEvent {
  id: string; created_at: string; event_type: string; result: "success" | "failure" | "denied";
  actor: string | null; target_type: string | null; target_id: string | null;
  correlation_id: string | null; ip_address: string | null; details: Record<string, unknown>;
}

export interface AdminMetrics {
  documents_by_status: Record<string, number>;
  users: { total: number; active: number };
  failed_access_last_24h: Record<string, number>;
  model_config_problems: string[];
  runtime: {
    counters: Record<string, number>;
    timers: Record<string, { count: number; avg_ms: number; max_ms: number }>;
    derived: Record<string, number | null>;
    scope: string;
  };
}

export interface PassagePreview {
  document_id: string; filename: string; classification: Classification; location: string; text: string;
}
