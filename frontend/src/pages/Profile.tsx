import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  analyzeCv,
  correctSkill,
  deleteCv,
  getCandidateProfile,
  listCvs,
  removeSkill,
  uploadCv,
} from "../api/endpoints";
import { Button, Card, ErrorBanner, SectionTitle, inputClass } from "../components/ui";

export function Profile() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [newSkill, setNewSkill] = useState("");

  const cvsQuery = useQuery({ queryKey: ["cvs"], queryFn: listCvs });
  const profileQuery = useQuery({ queryKey: ["candidate-profile"], queryFn: getCandidateProfile });

  // A correction rewrites the profile and rescores every job in the background,
  // so the fresh profile comes straight back from the mutation.
  const correctSkillMutation = useMutation({
    mutationFn: correctSkill,
    onSuccess: (profile) => {
      setNewSkill("");
      queryClient.setQueryData(["candidate-profile"], profile);
    },
  });

  const removeSkillMutation = useMutation({
    mutationFn: removeSkill,
    onSuccess: (profile) => queryClient.setQueryData(["candidate-profile"], profile),
  });

  const uploadMutation = useMutation({
    mutationFn: uploadCv,
    onSuccess: () => {
      setUploadError(null);
      queryClient.invalidateQueries({ queryKey: ["cvs"] });
    },
    onError: (error: unknown) =>
      setUploadError(error instanceof ApiError ? error.message : "Upload failed"),
  });

  const analyzeMutation = useMutation({
    mutationFn: analyzeCv,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["candidate-profile"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteCv,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cvs"] }),
  });

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
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium">{cv.filename}</span>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-500">
                    {new Date(cv.uploaded_at).toLocaleString()}
                  </span>
                  <button
                    type="button"
                    onClick={() => deleteMutation.mutate(cv.id)}
                    disabled={deleteMutation.isPending && deleteMutation.variables === cv.id}
                    title="Delete this CV"
                    aria-label={`Delete ${cv.filename}`}
                    className="text-slate-400 transition hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
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
                  : "Failed to delete CV"
              }
            />
          </div>
        )}

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

      {profileQuery.data && (
        <Card>
          <SectionTitle>Candidate profile</SectionTitle>
          {profileQuery.data.generated_by && (
            <p className="mb-3 -mt-2 text-xs text-slate-400">
              Analyzed using {profileQuery.data.generated_by}
            </p>
          )}
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-slate-500">Experience</dt>
              <dd>{profileQuery.data.experience_years} years</dd>
            </div>
            <div>
              <dt className="text-slate-500">Roles</dt>
              <dd>{profileQuery.data.roles.join(", ") || "—"}</dd>
            </div>
          </dl>
          <div className="mt-3">
            <p className="mb-1 text-sm text-slate-500">Skills</p>
            <div className="flex flex-wrap gap-1.5">
              {profileQuery.data.skills.map((skill) => (
                <span
                  key={skill.name}
                  className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
                    skill.source === "user" ? "bg-sky-100 text-sky-900" : "bg-slate-100"
                  }`}
                  title={skill.source === "user" ? `${skill.level} · your own edit` : skill.level}
                >
                  {skill.name}
                  <button
                    type="button"
                    aria-label={`Remove ${skill.name}`}
                    title="Not one of my skills — remove it from matching"
                    className="text-slate-400 hover:text-red-600"
                    disabled={removeSkillMutation.isPending}
                    onClick={() => removeSkillMutation.mutate(skill.name)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <form
              className="mt-2 flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (newSkill.trim()) correctSkillMutation.mutate(newSkill.trim());
              }}
            >
              <input
                className={inputClass}
                placeholder="Add a skill the CV didn't mention"
                value={newSkill}
                onChange={(event) => setNewSkill(event.target.value)}
              />
              <Button
                type="submit"
                className="bg-slate-600 whitespace-nowrap hover:bg-slate-500"
                disabled={!newSkill.trim() || correctSkillMutation.isPending}
              >
                Add
              </Button>
            </form>
            <p className="mt-1 text-xs text-slate-400">
              Your edits are remembered and re-applied every time this CV is analyzed again.
              Bringing a removed skill back means re-analyzing the CV.
            </p>
            {(correctSkillMutation.isError || removeSkillMutation.isError) && (
              <div className="mt-2">
                <ErrorBanner message="Failed to save that change" />
              </div>
            )}
          </div>
          {profileQuery.data.achievements.length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-sm text-slate-500">Achievements</p>
              <ul className="list-inside list-disc text-sm">
                {profileQuery.data.achievements.map((achievement) => (
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
