import { CLASSIFICATIONS, type Classification, type Role, type User } from "../types";
import { Field, inputCls } from "./ui";
import { titleCase } from "../utils/format";

export interface AclState {
  allowed_roles: string[]; user_grants: string[]; classification: Classification; department: string; owner_team: string;
}

/** Selection UI only. The server re-validates every value (unknown roles, escalation, classification rules). */
export default function AclEditor({ value, onChange, roles, users, isAdmin }: {
  value: AclState; onChange: (v: AclState) => void; roles: Role[]; users: User[]; isAdmin: boolean;
}) {
  const toggle = (list: string[], item: string) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  return (
    <div className="space-y-4">
      <Field label="Classification">
        <select className={inputCls} value={value.classification} onChange={(e) => onChange({ ...value, classification: e.target.value as Classification })}>
          {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
        </select>
      </Field>
      <fieldset>
        <legend className="mb-1 text-sm font-medium text-slate-700">Roles that can read this document</legend>
        <div className="flex flex-wrap gap-2">
          {roles.map((r) => (
            <label key={r.name} className={`cursor-pointer rounded-lg border px-3 py-1.5 text-sm ${value.allowed_roles.includes(r.name) ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-300 text-slate-600"}`}>
              <input type="checkbox" className="sr-only" checked={value.allowed_roles.includes(r.name)} onChange={() => onChange({ ...value, allowed_roles: toggle(value.allowed_roles, r.name) })} />
              {r.name}
            </label>
          ))}
        </div>
        <p className="mt-1 text-xs text-slate-500">Private by default: nobody can read it until you select at least one role or user.</p>
      </fieldset>
      {isAdmin && (
        <fieldset>
          <legend className="mb-1 text-sm font-medium text-slate-700">Individual user grants (optional)</legend>
          <div className="max-h-36 space-y-1 overflow-auto rounded-lg border border-slate-200 p-2">
            {users.filter((u) => u.is_active).map((u) => (
              <label key={u.id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={value.user_grants.includes(u.id)} onChange={() => onChange({ ...value, user_grants: toggle(value.user_grants, u.id) })} />
                {u.full_name} <span className="text-slate-400">{u.email}</span>
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Owner team (optional)"><input className={inputCls} value={value.owner_team} onChange={(e) => onChange({ ...value, owner_team: e.target.value })} maxLength={64} /></Field>
        <Field label="Department restriction" hint="Only users in this department"><input className={inputCls} value={value.department} onChange={(e) => onChange({ ...value, department: e.target.value })} maxLength={64} /></Field>
      </div>
    </div>
  );
}
