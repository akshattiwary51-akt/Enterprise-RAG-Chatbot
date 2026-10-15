import ReactMarkdown from "react-markdown";
import type { Citation } from "../types";

/**
 * Renders assistant Markdown. Raw HTML is NOT rendered (react-markdown default) and only http(s)/mailto/our
 * internal `cite:` scheme links survive. Citation markers [S1] become buttons, but ONLY for ids present in
 * the server-validated `citations` list; anything else is left as plain text.
 */
export default function MessageContent({ text, citations, onCite }: { text: string; citations: Citation[]; onCite: (c: Citation) => void }) {
  const byId = new Map(citations.map((c) => [c.id, c]));
  const prepared = text.replace(/\[(S\d+)\]/g, (m, id: string) => (byId.has(id) ? `[${id}](cite:${id})` : m));
  return (
    <div className="md-body text-[15px] leading-relaxed">
      <ReactMarkdown
        urlTransform={(url) => (url.startsWith("cite:") || /^(https?:|mailto:)/i.test(url) ? url : "")}
        components={{
          a({ href, children }) {
            if (href?.startsWith("cite:")) {
              const c = byId.get(href.slice(5));
              return (
                <button type="button" onClick={() => c && onCite(c)} title={c ? `${c.filename}${c.location ? " · " + c.location : ""}` : ""}
                  className="mx-0.5 inline-flex -translate-y-px items-center rounded-md bg-brand-50 px-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-100">
                  {children}
                </button>
              );
            }
            return <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-brand-700 underline">{children}</a>;
          },
        }}
      >
        {prepared}
      </ReactMarkdown>
    </div>
  );
}
