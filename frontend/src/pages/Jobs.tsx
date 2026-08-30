import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { listJobs, rescoreJob } from "../api/endpoints";
import { Button, Card, ErrorBanner, SectionTitle } from "../components/ui";

const PAGE_SIZE = 25;

export function Jobs() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);

  const jobsQuery = useQuery({
    queryKey: ["jobs", offset],
    queryFn: () => listJobs(PAGE_SIZE, offset),
    placeholderData: (previous) => previous,
  });
  const jobs = jobsQuery.data?.items ?? [];
  const total = jobsQuery.data?.total ?? 0;

  const unscoredJobIds = jobs.filter((job) => job.practical_fit === null).map((job) => job.id);

  const scoreAllMutation = useMutation({
    mutationFn: () => Promise.all(unscoredJobIds.map((jobId) => rescoreJob(jobId))),
    onSuccess: () => {
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["jobs", offset] });
      }, 3000);
    },
  });

  const hasPrevious = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <SectionTitle>Jobs</SectionTitle>
        {unscoredJobIds.length > 0 && (
          <Button onClick={() => scoreAllMutation.mutate()} disabled={scoreAllMutation.isPending}>
            {scoreAllMutation.isPending
              ? "Queuing…"
              : `Score unscored on this page (${unscoredJobIds.length})`}
          </Button>
        )}
      </div>
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
    </Card>
  );
}
