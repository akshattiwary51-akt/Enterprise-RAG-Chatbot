import { History, MessageSquarePlus, Send, ShieldAlert, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import CitationModal from "../components/CitationModal";
import MessageContent from "../components/MessageContent";
import { Button, EmptyState, ErrorBanner, Spinner } from "../components/ui";
import { describeError } from "../services/api";
import { chatApi } from "../services/endpoints";
import type { ChatMessage, Citation, ConversationSummary } from "../types";

const SUGGESTIONS = ["What is our vacation policy?", "How do deployments work?", "Summarize the latest budget figures I can access"];

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cite, setCite] = useState<Citation | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const loadList = useCallback(() => chatApi.conversations().then((p) => setConversations(p.items)).catch(() => undefined), []);
  useEffect(() => { void loadList(); }, [loadList]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function open(id: string) {
    setError(null); setShowHistory(false);
    try { const c = await chatApi.conversation(id); setActiveId(c.id); setMessages(c.messages); }
    catch (e) { setError(describeError(e).message); }
  }
  function fresh() { setActiveId(null); setMessages([]); setError(null); setShowHistory(false); }
  async function remove(id: string) {
    try { await chatApi.remove(id); if (id === activeId) fresh(); await loadList(); } catch (e) { setError(describeError(e).message); }
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true); setError(null); setInput("");
    const optimistic: ChatMessage = { id: `tmp-${Date.now()}`, role: "user", content: q, citations: [], no_answer: false, created_at: new Date().toISOString() };
    setMessages((m) => [...m, optimistic]);
    try {
      const res = await chatApi.send(q, activeId);
      setActiveId(res.conversation_id);
      setMessages((m) => [...m, res.message]);
      void loadList();
    } catch (e) {
      setMessages((m) => m.filter((x) => x.id !== optimistic.id));
      setInput(q);
      setError(describeError(e).message);
    } finally { setBusy(false); }
  }
  const onSubmit = (e: FormEvent) => { e.preventDefault(); void send(input); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(input); } };

  const historyPanel = (
    <div className="flex h-full flex-col">
      <div className="p-3"><Button variant="secondary" className="w-full" onClick={fresh}><MessageSquarePlus className="h-4 w-4" />New conversation</Button></div>
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-auto px-2 pb-3">
        {conversations.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate-400">No conversations yet</li>}
        {conversations.map((c) => (
          <li key={c.id} className={`group flex items-center rounded-lg ${c.id === activeId ? "bg-brand-50" : "hover:bg-slate-100"}`}>
            <button onClick={() => void open(c.id)} className="min-w-0 flex-1 truncate px-3 py-2 text-left text-sm">{c.title}</button>
            <button onClick={() => void remove(c.id)} aria-label={`Delete conversation ${c.title}`} className="mr-1 rounded p-1.5 text-slate-400 opacity-60 hover:text-red-600 group-hover:opacity-100"><Trash2 className="h-3.5 w-3.5" /></button>
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white lg:block">{historyPanel}</aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2 lg:hidden">
          <Button variant="ghost" onClick={() => setShowHistory((s) => !s)}><History className="h-4 w-4" />History</Button>
          <Button variant="ghost" onClick={fresh}><MessageSquarePlus className="h-4 w-4" />New</Button>
        </div>
        {showHistory && <div className="max-h-72 border-b border-slate-200 bg-white lg:hidden">{historyPanel}</div>}

        <div className="min-h-0 flex-1 overflow-auto">
          <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
            {messages.length === 0 && !busy && (
              <div className="pt-10">
                <EmptyState icon={<ShieldAlert className="h-10 w-10" />} title="Ask about your company documents"
                  hint="Answers come only from documents you are authorized to access, with citations you can open." />
                <div className="mt-2 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => <button key={s} onClick={() => void send(s)} className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:border-brand-600 hover:text-brand-700">{s}</button>)}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={m.role === "user" ? "flex justify-end" : "flex"}>
                {m.role === "user" ? (
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-700 px-4 py-2.5 text-[15px] text-white">{m.content}</div>
                ) : (
                  <div className={`max-w-full rounded-2xl rounded-bl-md border px-4 py-3 shadow-sm ${m.no_answer || m.redacted ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
                    <MessageContent text={m.content} citations={m.citations} onCite={setCite} />
                    {m.citations.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100 pt-2.5">
                        {m.citations.map((c) => (
                          <button key={c.id} onClick={() => setCite(c)} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600 hover:border-brand-600">
                            <strong className="text-brand-700">{c.id}</strong> {c.filename}{c.location ? ` · ${c.location}` : ""}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner className="h-4 w-4" />Searching authorized documents…</div>}
            <div ref={endRef} />
          </div>
        </div>

        <div className="border-t border-slate-200 bg-white px-4 py-3">
          <div className="mx-auto max-w-3xl space-y-2">
            <ErrorBanner message={error} onClose={() => setError(null)} />
            <form onSubmit={onSubmit} className="flex items-end gap-2">
              <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={1} maxLength={4000} aria-label="Your question"
                placeholder="Ask a question…" className="max-h-40 min-h-[42px] flex-1 resize-none rounded-xl border border-slate-300 px-3.5 py-2.5 text-[15px] focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600" />
              <Button type="submit" disabled={!input.trim()} busy={busy} aria-label="Send" className="h-[42px] w-[42px] !p-0"><Send className="h-4 w-4" /></Button>
            </form>
            <p className="text-center text-xs text-slate-400">Answers are generated from authorized documents and may contain errors — check the citations.</p>
          </div>
        </div>
      </div>
      {cite && <CitationModal citation={cite} onClose={() => setCite(null)} />}
    </div>
  );
}
