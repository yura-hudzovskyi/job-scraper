import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { getJob, getJobMatch, rescoreJob } from "../api/endpoints";
import { Button, Card, ErrorBanner, SectionTitle } from "../components/ui";

// Rescoring runs a background LLM call that can take anywhere from a couple of
// seconds (Gemini) to 60-90s (a 14B-class Ollama fallback on CPU, see
// docs/deployment.md) — polling briefly and giving up beats both "assume it's
// done after a fixed 3s" (wrong: showed stale data) and polling forever.
const RESCORE_POLL_INTERVAL_MS = 4000;
const RESCORE_POLL_TIMEOUT_MS = 120_000;

const BREAKDOWN_LABELS: Record<string, string> = {
  skills: "Skills",
  role: "Role",
  experience: "Experience",
  semantic_fit: "Semantic fit",
  salary: "Salary",
  location: "Location",
  transferable_skills: "Transferable skills",
  preferences: "Preferences",
};

// These three components only carry a real signal when the job actually had
// skills to check against (SkillMatcher's fallback for "nothing to extract" is
// a fabricated 100 — see docs/matching-engine.md's "No extracted skills is not a
// perfect match"). Showing that as a literal "100%" reads as a confident, fully-
// assessed match when nothing was actually checked, which is worse than showing
// nothing at all.
const SKILL_DEPENDENT_KEYS = new Set(["skills", "transferable_skills", "preferences"]);

export function JobDetails() {
  const { jobId } = useParams<{ jobId: string }>();
  const queryClient = useQueryClient();
  if (!jobId) return null;

  // Rescoring is a background Celery task, not a synchronous call — the button
  // used to just wait a fixed 3s and invalidate once, which silently showed
  // stale data whenever scoring took longer than that (routine now that scoring
  // can fall back to a slow local Ollama call). Instead: poll until the match's
  // scored_at timestamp actually moves past what it was before this rescore, or
  // give up after RESCORE_POLL_TIMEOUT_MS.
  const [pollDeadline, setPollDeadline] = useState<number | null>(null);
  const scoredAtBeforeRescore = useRef<string | null>(null);

  const jobQuery = useQuery({ queryKey: ["job", jobId], queryFn: () => getJob(jobId) });
  const matchQuery = useQuery({
    queryKey: ["job-match", jobId],
    queryFn: () => getJobMatch(jobId),
    retry: false,
    refetchInterval: pollDeadline !== null ? RESCORE_POLL_INTERVAL_MS : false,
  });

  useEffect(() => {
    if (pollDeadline === null) return;
    const finished =
      matchQuery.data !== undefined && matchQuery.data.scored_at !== scoredAtBeforeRescore.current;
    if (finished || Date.now() >= pollDeadline) {
      setPollDeadline(null);
    }
  }, [matchQuery.data, pollDeadline]);

  const isRescoring = pollDeadline !== null;

  const rescoreMutation = useMutation({
    mutationFn: () => rescoreJob(jobId),
    onSuccess: () => {
      scoredAtBeforeRescore.current = matchQuery.data?.scored_at ?? null;
      setPollDeadline(Date.now() + RESCORE_POLL_TIMEOUT_MS);
      queryClient.invalidateQueries({ queryKey: ["job-match", jobId] });
    },
  });

  const notScoredYet = matchQuery.error instanceof ApiError && matchQuery.error.status === 404;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        {jobQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {jobQuery.data && (
          <>
            <SectionTitle>{jobQuery.data.title}</SectionTitle>
            <p className="mb-3 text-sm text-slate-600">{jobQuery.data.company}</p>
            <p className="whitespace-pre-line text-sm text-slate-700">
              {jobQuery.data.description}
            </p>
          </>
        )}
      </Card>

      <Card>
        <SectionTitle>Match</SectionTitle>

        {notScoredYet && (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-slate-600">
              Not scored yet — needs an analyzed CV and preferences set first.
            </p>
            <Button
              onClick={() => rescoreMutation.mutate()}
              disabled={rescoreMutation.isPending || isRescoring}
              className="w-fit"
            >
              {rescoreMutation.isPending || isRescoring ? "Scoring…" : "Score this job"}
            </Button>
            {isRescoring && (
              <p className="text-sm text-slate-500">
                Waiting for the score to come back — this can take up to a couple of minutes.
              </p>
            )}
            {rescoreMutation.isError && (
              <ErrorBanner
                message={
                  rescoreMutation.error instanceof ApiError
                    ? rescoreMutation.error.message
                    : "Failed to queue scoring"
                }
              />
            )}
          </div>
        )}

        {matchQuery.error && !notScoredYet && (
          <ErrorBanner
            message={
              matchQuery.error instanceof ApiError ? matchQuery.error.message : "Failed to load"
            }
          />
        )}

        {matchQuery.data && (
          <div className="flex flex-col gap-4">
            <div className="flex items-baseline gap-6">
              <div>
                <p className="text-3xl font-bold">{matchQuery.data.practical_fit.toFixed(0)}%</p>
                <p className="text-sm text-slate-500">Practical fit</p>
              </div>
              <div>
                <p className="text-xl font-semibold text-slate-600">
                  {matchQuery.data.requirement_match.toFixed(0)}%
                </p>
                <p className="text-sm text-slate-500">Requirement match</p>
              </div>
              {matchQuery.data.recommendation && (
                <span className="rounded-full bg-slate-900 px-3 py-1 text-sm text-white uppercase">
                  {matchQuery.data.recommendation}
                </span>
              )}
            </div>

            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              {Object.entries(matchQuery.data.breakdown).map(([key, value]) => {
                const notActuallyAssessed =
                  !matchQuery.data!.skills_source &&
                  !matchQuery.data!.scored_by?.startsWith("AI (") &&
                  SKILL_DEPENDENT_KEYS.has(key);
                return (
                  <div key={key} className="flex items-center justify-between">
                    <span className="text-slate-600">{BREAKDOWN_LABELS[key] ?? key}</span>
                    <span
                      className={notActuallyAssessed ? "text-slate-400 italic" : "font-medium"}
                      title={notActuallyAssessed ? "No skills extracted — not actually assessed" : undefined}
                    >
                      {notActuallyAssessed ? "N/A" : `${Number(value).toFixed(0)}%`}
                    </span>
                  </div>
                );
              })}
            </div>

            {matchQuery.data.strengths.length > 0 && (
              <div>
                <p className="mb-1 text-sm text-slate-500">Matched skills</p>
                <div className="flex flex-wrap gap-1.5">
                  {matchQuery.data.strengths.map((strength) => (
                    <span
                      key={strength.label}
                      title={strength.detail}
                      className="rounded-full bg-green-100 px-2.5 py-1 text-xs text-green-800"
                    >
                      {strength.label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {matchQuery.data.gaps.length > 0 && (
              <div>
                <p className="mb-1 text-sm text-slate-500">Gaps</p>
                <div className="flex flex-wrap gap-1.5">
                  {matchQuery.data.gaps.map((gap) => (
                    <span
                      key={gap.label}
                      title={gap.critical ? "Required — a critical gap" : "Nice-to-have"}
                      className={`rounded-full px-2.5 py-1 text-xs ${
                        gap.critical
                          ? "bg-red-100 text-red-800"
                          : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {gap.label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {matchQuery.data.scored_by && (
              <p className="text-xs text-slate-400">
                {matchQuery.data.scored_by.startsWith("AI (")
                  ? `Scored using ${matchQuery.data.scored_by}`
                  : "Scored using the deterministic pipeline (no AI match available for this job)"}
              </p>
            )}

            {matchQuery.data.skills_source ? (
              <p className="text-xs text-slate-400">
                Skills identified using {matchQuery.data.skills_source}
              </p>
            ) : (
              !matchQuery.data.scored_by?.startsWith("AI (") && (
                <p className="text-xs text-amber-600">
                  ⚠️ No skills could be extracted for this job — this match is less reliable
                  than usual (see the N/A fields above).
                </p>
              )
            )}

            <Button
              onClick={() => rescoreMutation.mutate()}
              disabled={rescoreMutation.isPending || isRescoring}
              className="w-fit bg-slate-600 hover:bg-slate-500"
            >
              {rescoreMutation.isPending || isRescoring ? "Rescoring…" : "Rescore"}
            </Button>
            {isRescoring && (
              <p className="text-sm text-slate-500">
                Waiting for the new score to come back — this can take up to a couple of minutes.
              </p>
            )}
            {rescoreMutation.isError && (
              <ErrorBanner
                message={
                  rescoreMutation.error instanceof ApiError
                    ? rescoreMutation.error.message
                    : "Failed to queue scoring"
                }
              />
            )}
          </div>
        )}
      </Card>

      {matchQuery.data?.llm_assessment && (
        <Card>
          <div className="mb-3 flex items-center gap-3">
            <SectionTitle>Should I apply?</SectionTitle>
            <span className="rounded-full bg-slate-900 px-3 py-1 text-sm text-white uppercase">
              {matchQuery.data.llm_assessment.recommendation}
            </span>
            <span className="text-xs text-slate-500">
              {(matchQuery.data.llm_assessment.confidence * 100).toFixed(0)}% confidence ·{" "}
              {matchQuery.data.llm_assessment.interview_risk} interview risk
            </span>
          </div>
          <p className="mb-3 text-sm text-slate-700">{matchQuery.data.llm_assessment.summary}</p>

          {matchQuery.data.llm_assessment.critical_gaps.length > 0 && (
            <div className="mb-3">
              <p className="mb-1 text-sm text-slate-500">Critical gaps</p>
              <div className="flex flex-wrap gap-1.5">
                {matchQuery.data.llm_assessment.critical_gaps.map((gap) => (
                  <span
                    key={gap}
                    className="rounded-full bg-red-100 px-2.5 py-1 text-xs text-red-800"
                  >
                    {gap}
                  </span>
                ))}
              </div>
            </div>
          )}

          {matchQuery.data.llm_assessment.transferable_experience.length > 0 && (
            <div className="mb-3">
              <p className="mb-1 text-sm text-slate-500">Transferable experience</p>
              <div className="flex flex-wrap gap-1.5">
                {matchQuery.data.llm_assessment.transferable_experience.map((item) => (
                  <span
                    key={item}
                    className="rounded-full bg-green-100 px-2.5 py-1 text-xs text-green-800"
                  >
                    {item}
                  </span>
                ))}
              </div>
            </div>
          )}

          {matchQuery.data.llm_assessment.recommended_cv && (
            <p className="mb-3 text-sm text-slate-600">
              Recommended CV: <span className="font-medium">{matchQuery.data.llm_assessment.recommended_cv}</span>
            </p>
          )}

          <p className="text-xs text-slate-400">
            Assessed using {matchQuery.data.llm_assessment.model_label}
          </p>
        </Card>
      )}
    </div>
  );
}
