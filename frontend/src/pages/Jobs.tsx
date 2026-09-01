import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { listJobs, listOllamaModels, rescoreAllJobs, rescoreJob } from "../api/endpoints";
import { Button, Card, ErrorBanner, Field, Modal, SectionTitle, inputClass } from "../components/ui";

const PAGE_SIZE = 25;

export function Jobs() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [includeSkipped, setIncludeSkipped] = useState(false);
  const [isRescoreAllOpen, setIsRescoreAllOpen] = useState(false);
  const [rescoreAllModel, setRescoreAllModel] = useState("");

  const jobsQuery = useQuery({
    queryKey: ["jobs", offset, includeSkipped],
    queryFn: () => listJobs(PAGE_SIZE, offset, includeSkipped),
    placeholderData: (previous) => previous,
  });
  const jobs = jobsQuery.data?.items ?? [];
  const total = jobsQuery.data?.total ?? 0;

  const unscoredJobIds = jobs.filter((job) => job.practical_fit === null).map((job) => job.id);

  const scoreAllMutation = useMutation({
    mutationFn: () => Promise.all(unscoredJobIds.map((jobId) => rescoreJob(jobId))),
    onSuccess: () => {
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["jobs", offset, includeSkipped] });
      }, 3000);
    },
  });

  const ollamaModelsQuery = useQuery({
    queryKey: ["ollama-models"],
    queryFn: listOllamaModels,
    enabled: isRescoreAllOpen,
  });

  const rescoreAllMutation = useMutation({
    mutationFn: () => rescoreAllJobs(rescoreAllModel || undefined),
    onSuccess: () => {
      setIsRescoreAllOpen(false);
      setRescoreAllModel("");
    },
  });

  const hasPrevious = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <SectionTitle>Jobs</SectionTitle>
        <div className="flex items-center gap-2">
          {unscoredJobIds.length > 0 && (
            <Button
              onClick={() => scoreAllMutation.mutate()}
              disabled={scoreAllMutation.isPending}
            >
              {scoreAllMutation.isPending
                ? "Queuing…"
                : `Score unscored on this page (${unscoredJobIds.length})`}
            </Button>
          )}
          <Button
            className="bg-slate-600 hover:bg-slate-500"
            title="Re-extract skills and recompute the AI match for every vacancy in the database, not just this page. Uses Groq by default, falling back to local Ollama automatically once Groq's rate limit is hit. Runs in the background and can take a while."
            onClick={() => setIsRescoreAllOpen(true)}
          >
            Rescore all vacancies…
          </Button>
        </div>
      </div>
      {rescoreAllMutation.isSuccess && (
        <p className="mb-3 text-sm text-green-700">
          Queued — rescoring every vacancy in the background, this can take a while.
        </p>
      )}
      <label className="mb-3 flex w-fit items-center gap-2 text-sm text-slate-600">
        <input
          type="checkbox"
          checked={includeSkipped}
          onChange={(e) => {
            setIncludeSkipped(e.target.checked);
            setOffset(0);
          }}
        />
        Show all, including low matches
      </label>
      {scoreAllMutation.isSuccess && (
        <p className="mb-3 text-sm text-green-700">
          Queued — this runs in the background, refresh in a few seconds.
        </p>
      )}
      {scoreAllMutation.isError && (
        <div className="mb-3">
          <ErrorBanner message="Failed to queue scoring for one or more jobs" />
        </div>
      )}
      {jobsQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {jobsQuery.isError && <ErrorBanner message="Failed to load jobs" />}
      {jobs.length === 0 && !jobsQuery.isLoading && (
        <p className="text-sm text-slate-500">
          No jobs yet — trigger a sync on the Sources page, or wait for the next scheduled scrape.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {jobs.map((job) => (
          <li key={job.id}>
            <Link
              to={`/jobs/${job.id}`}
              className="block rounded border border-slate-200 p-3 hover:border-slate-400"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{job.title}</span>
                <div className="flex items-center gap-2">
                  {job.practical_fit !== null && (
                    <span className="rounded-full bg-slate-900 px-2 py-0.5 text-xs text-white">
                      {job.practical_fit.toFixed(0)}%
                    </span>
                  )}
                  {job.source_count > 1 && (
                    <span className="text-xs text-slate-500">{job.source_count} sources</span>
                  )}
                </div>
              </div>
              <p className="text-sm text-slate-600">{job.company}</p>
            </Link>
          </li>
        ))}
      </ul>
      {total > 0 && (
        <div className="mt-4 flex items-center justify-between">
          <p className="text-sm text-slate-500">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </p>
          <div className="flex gap-2">
            <Button
              className="bg-slate-600 hover:bg-slate-500"
              onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
              disabled={!hasPrevious}
            >
              Previous
            </Button>
            <Button
              className="bg-slate-600 hover:bg-slate-500"
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
              disabled={!hasNext}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {isRescoreAllOpen && (
        <Modal title="Rescore all vacancies" onClose={() => setIsRescoreAllOpen(false)}>
          <p className="mb-3 text-sm text-slate-600">
            Re-extracts skills and recomputes the AI match for every vacancy currently in the
            database — not just this page. Uses Groq by default (same provider as automatic
            per-scrape extraction and AI matching); if Groq's rate limit is hit mid-run, it
            automatically falls back to local Ollama for the rest. This runs in the background
            and can take a while for a large backlog. To change Groq's own model persistently
            instead of just this run's Ollama fallback, use the System page.
          </p>
          <Field label="Ollama model to fall back to (optional)">
            {ollamaModelsQuery.data && ollamaModelsQuery.data.models.length > 0 ? (
              <select
                className={inputClass}
                value={rescoreAllModel}
                onChange={(e) => setRescoreAllModel(e.target.value)}
              >
                <option value="">Server default</option>
                {ollamaModelsQuery.data.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={inputClass}
                placeholder="Server default (leave blank), e.g. qwen2.5:14b"
                value={rescoreAllModel}
                onChange={(e) => setRescoreAllModel(e.target.value)}
              />
            )}
          </Field>
          {ollamaModelsQuery.isLoading && (
            <p className="mt-1 text-xs text-slate-400">Loading available Ollama models…</p>
          )}
          {ollamaModelsQuery.data && ollamaModelsQuery.data.models.length === 0 && (
            <p className="mt-1 text-xs text-slate-400">
              No Ollama models detected on the server (or a non-Ollama provider is configured) —
              leave blank to use the server default, or type a model tag manually. Only used if
              Groq isn't configured or its rate limit is hit.
            </p>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button
              className="bg-slate-600 hover:bg-slate-500"
              onClick={() => setIsRescoreAllOpen(false)}
              disabled={rescoreAllMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => rescoreAllMutation.mutate()}
              disabled={rescoreAllMutation.isPending}
            >
              {rescoreAllMutation.isPending ? "Queuing…" : "Confirm"}
            </Button>
          </div>
          {rescoreAllMutation.isError && (
            <div className="mt-2">
              <ErrorBanner message="Failed to queue rescore-all" />
            </div>
          )}
        </Modal>
      )}
    </Card>
  );
}
