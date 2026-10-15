import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { Badge, Button, ErrorBanner, Modal, Spinner } from "./ui";
import { describeError } from "../services/api";
import { documentsApi } from "../services/endpoints";
import type { Citation, PassagePreview } from "../types";

/** Fetches the passage through the ACL-checked preview endpoint; a revoked user simply gets "no longer available". */
export default function CitationModal({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [preview, setPreview] = useState<PassagePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!citation.passage_id) return;
    documentsApi.preview(citation.document_id, citation.passage_id).then(setPreview)
      .catch((e) => setError(describeError(e).status === 404 ? "This source is no longer available to you." : describeError(e).message));
  }, [citation]);

  return (
    <Modal title={citation.filename} onClose={onClose} wide>
      <div className="space-y-3">
        {citation.location && <p className="text-sm text-slate-500">{citation.location}</p>}
        <ErrorBanner message={error} />
        {!citation.passage_id && <p className="text-sm text-slate-600">This statement was computed exactly from the spreadsheet contents ({citation.location}).</p>}
        {citation.passage_id && !preview && !error && <Spinner className="h-5 w-5 text-slate-400" />}
        {preview && (
          <>
            <Badge value={preview.classification} />
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-800">{preview.text}</pre>
          </>
        )}
        <Button variant="secondary" onClick={() => documentsApi.download(citation.document_id, citation.filename).catch((e) => setError(describeError(e).message))}>
          <Download className="h-4 w-4" />Download original
        </Button>
      </div>
    </Modal>
  );
}
