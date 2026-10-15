import { FileText, LogOut, Menu, MessageSquare, ShieldCheck, UserCircle2, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export default function AppLayout() {
  const { user, logout, can } = useAuth();
  const [open, setOpen] = useState(false);

  const links = [
    { to: "/", label: "Assistant", icon: MessageSquare, show: true, end: true },
    { to: "/documents", label: "Documents", icon: FileText, show: true },
    { to: "/admin", label: "Administration", icon: ShieldCheck, show: can("users.manage") || can("audit.view") },
    { to: "/profile", label: "Profile", icon: UserCircle2, show: true },
  ].filter((l) => l.show);

  const nav = (
    <nav className="flex flex-1 flex-col gap-1 px-3" aria-label="Main">
      {links.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)}
          className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100"}`}>
          <Icon className="h-4 w-4" aria-hidden="true" />{label}
        </NavLink>
      ))}
    </nav>
  );

  const account = (
    <div className="border-t border-slate-200 p-3">
      <div className="mb-2 px-2">
        <p className="truncate text-sm font-medium">{user?.full_name}</p>
        <p className="truncate text-xs text-slate-500">{user?.roles.join(", ")}</p>
      </div>
      <button onClick={() => void logout()} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100">
        <LogOut className="h-4 w-4" aria-hidden="true" />Sign out
      </button>
    </div>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <Brand />{nav}{account}
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-slate-900/40" />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
            <Brand />{nav}{account}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 md:hidden">
          <button onClick={() => setOpen((o) => !o)} aria-label={open ? "Close menu" : "Open menu"} className="rounded p-1 hover:bg-slate-100">
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <span className="font-semibold">Knowledge Assistant</span>
        </header>
        <main className="min-h-0 flex-1 overflow-auto"><Outlet /></main>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-5 py-5">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand-700 text-white"><MessageSquare className="h-4 w-4" /></div>
      <span className="text-[15px] font-semibold tracking-tight">Knowledge Assistant</span>
    </div>
  );
}
