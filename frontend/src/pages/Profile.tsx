import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ApiError } from "../api/client";
import { deleteCv, getActiveCv, listCvs, uploadCv } from "../api/endpoints";
import { Badge, Card, ErrorBanner, InfoBanner, SectionTitle } from "../components/ui";

export function Profile() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const cvsQuery = useQuery({ queryKey: ["cvs"], queryFn: listCvs });
  const activeQuery = useQuery({ queryKey: ["active-cv"], queryFn: getActiveCv });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["cvs"] });
    queryClient.invalidateQueries({ queryKey: ["active-cv"] });
  };

  const uploadMutation = useMutation({
    mutationFn: uploadCv,
    onSuccess: () => {
      setUploadError(null);
      refresh();
    },
    onError: (error: unknown) =>
      setUploadError(error instanceof ApiError ? error.message : "Upload failed"),
  });

  const deleteMutation = useMutation({ mutationFn: deleteCv, onSuccess: refresh });

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>CV</SectionTitle>
        <p className="mb-3 text-sm text-slate-600">
          Your CV is the entire candidate side of the pipeline: its text is embedded and handed to
          the reranker as-is. Nothing is extracted from it, so nothing can be extracted wrongly.
          The most recently uploaded CV is the active one.
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.pdf,.docx"
          onChange={() => {
            const file = fileInputRef.current?.files?.[0];
            if (file) uploadMutation.mutate(file);
          }}
          disabled={uploadMutation.isPending}
          className="text-sm"
        />
        <p className="mt-2 text-xs text-slate-500">
          Accepts .txt, .pdf or .docx. A scanned PDF with no text layer is rejected at upload rather
          than silently matching nothing.
        </p>
        {uploadMutation.isPending && <p className="mt-2 text-sm text-slate-500">Uploading…</p>}
        {uploadMutation.isSuccess && (
          <div className="mt-3">
            <InfoBanner tone="ok">
              Uploaded and now active — re-matching every vacancy against it in the background.
            </InfoBanner>
          </div>
        )}
        {uploadError && (
          <div className="mt-2">
            <ErrorBanner message={uploadError} />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Uploaded CVs</SectionTitle>
        {cvsQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {cvsQuery.data?.length === 0 && (
          <p className="text-sm text-slate-500">No CVs uploaded yet.</p>
        )}
        <ul className="flex flex-col gap-2">
          {cvsQuery.data?.map((cv) => (
            <li key={cv.id} className="rounded border border-slate-200 p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{cv.filename}</span>
                  {cv.active && <Badge tone="ok">active</Badge>}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-500">
                    {cv.characters.toLocaleString()} chars ·{" "}
                    {new Date(cv.uploaded_at).toLocaleString()}
                  </span>
                  <button
                    type="button"
                    onClick={() => deleteMutation.mutate(cv.id)}
                    disabled={deleteMutation.isPending && deleteMutation.variables === cv.id}
                    title="Delete this CV"
                    aria-label={`Delete ${cv.filename}`}
                    className="text-slate-400 transition hover:text-red-600 disabled:opacity-50"
                  >
                    ✕
                  </button>
                </div>
              </div>
              <p className="mt-1 line-clamp-2 text-sm text-slate-600">{cv.text_preview}</p>
            </li>
          ))}
        </ul>
        {deleteMutation.isError && (
          <div className="mt-2">
            <ErrorBanner
              message={
                deleteMutation.error instanceof ApiError
                  ? deleteMutation.error.message
                  : "Failed to delete the CV"
              }
            />
          </div>
        )}
      </Card>

      {activeQuery.data?.cv && (
        <Card>
          <SectionTitle>What the models read</SectionTitle>
          <p className="mb-3 text-sm text-slate-600">
            Your CV plus the parts of your preferences that describe what you want — the exact text
            that becomes your query vector and the reranker's query. Constraints (blocked stack,
            salary floor, blacklists) are deliberately absent: those are enforced by the filters, and
            putting them here too would apply the same fact twice.
          </p>
          <pre className="max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs whitespace-pre-wrap text-slate-700">
            {activeQuery.data.model_document}
          </pre>
        </Card>
      )}
    </div>
  );
}
