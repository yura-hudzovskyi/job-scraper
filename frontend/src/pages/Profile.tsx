import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ApiError } from "../api/client";
import { analyzeCv, listCvs, uploadCv } from "../api/endpoints";
import { Button, Card, ErrorBanner, SectionTitle } from "../components/ui";

export function Profile() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const cvsQuery = useQuery({ queryKey: ["cvs"], queryFn: listCvs });

  const uploadMutation = useMutation({
    mutationFn: uploadCv,
    onSuccess: () => {
      setUploadError(null);
      queryClient.invalidateQueries({ queryKey: ["cvs"] });
    },
    onError: (error: unknown) =>
      setUploadError(error instanceof ApiError ? error.message : "Upload failed"),
  });

  const analyzeMutation = useMutation({ mutationFn: analyzeCv });

  function handleFileChange() {
    const file = fileInputRef.current?.files?.[0];
    if (file) {
      uploadMutation.mutate(file);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Upload CV</SectionTitle>
        <p className="mb-3 text-sm text-slate-600">Accepts .txt, .pdf, or .docx.</p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.pdf,.docx"
          onChange={handleFileChange}
          disabled={uploadMutation.isPending}
          className="text-sm"
        />
        {uploadMutation.isPending && <p className="mt-2 text-sm text-slate-500">Uploading…</p>}
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
              <div className="flex items-center justify-between">
                <span className="font-medium">{cv.filename}</span>
                <span className="text-xs text-slate-500">
                  {new Date(cv.uploaded_at).toLocaleString()}
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-sm text-slate-600">{cv.text_preview}</p>
            </li>
          ))}
        </ul>

        {cvsQuery.data && cvsQuery.data.length > 0 && (
          <div className="mt-4">
            <Button onClick={() => analyzeMutation.mutate()} disabled={analyzeMutation.isPending}>
              {analyzeMutation.isPending ? "Analyzing…" : "Analyze most recent CV"}
            </Button>
            {analyzeMutation.isError && (
              <div className="mt-2">
                <ErrorBanner
                  message={
                    analyzeMutation.error instanceof ApiError
                      ? analyzeMutation.error.message
                      : "Analysis failed"
                  }
                />
              </div>
            )}
          </div>
        )}
      </Card>

      {analyzeMutation.data && (
        <Card>
          <SectionTitle>Candidate profile</SectionTitle>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-slate-500">Experience</dt>
              <dd>{analyzeMutation.data.experience_years} years</dd>
            </div>
            <div>
              <dt className="text-slate-500">Roles</dt>
              <dd>{analyzeMutation.data.roles.join(", ") || "—"}</dd>
            </div>
          </dl>
          <div className="mt-3">
            <p className="mb-1 text-sm text-slate-500">Skills</p>
            <div className="flex flex-wrap gap-1.5">
              {analyzeMutation.data.skills.map((skill) => (
                <span
                  key={skill.name}
                  className="rounded-full bg-slate-100 px-2.5 py-1 text-xs"
                  title={skill.level}
                >
                  {skill.name}
                </span>
              ))}
            </div>
          </div>
          {analyzeMutation.data.achievements.length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-sm text-slate-500">Achievements</p>
              <ul className="list-inside list-disc text-sm">
                {analyzeMutation.data.achievements.map((achievement) => (
                  <li key={achievement}>{achievement}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
