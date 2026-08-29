import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { getJobMatch, listJobs, rescoreJob } from "../api/endpoints";
import { Button, Card, ErrorBanner, SectionTitle } from "../components/ui";

export function Jobs() {
  const queryClient = useQueryClient();
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs });
  const jobs = jobsQuery.data ?? [];

  const matchQueries = useQueries({
    queries: jobs.map((job) => ({
      queryKey: ["job-match", job.id],
      queryFn: () => getJobMatch(job.id),
      retry: false,
    })),
  });

  const unscoredJobIds = jobs
    .filter((_, index) => matchQueries[index]?.error instanceof ApiError)
    .map((job) => job.id);

  const scoreAllMutation = useMutation({
    mutationFn: () => Promise.all(unscoredJobIds.map((jobId) => rescoreJob(jobId))),
    onSuccess: () => {
      setTimeout(() => {
        for (const jobId of unscoredJobIds) {
          queryClient.invalidateQueries({ queryKey: ["job-match", jobId] });
        }
      }, 3000);
    },
  });

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <SectionTitle>Jobs</SectionTitle>
        {unscoredJobIds.length > 0 && (
          <Button onClick={() => scoreAllMutation.mutate()} disabled={scoreAllMutation.isPending}>
            {scoreAllMutation.isPending
              ? "Queuing…"
              : `Score all unscored (${unscoredJobIds.length})`}
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
      {jobs.length === 0 && !jobsQuery.isLoading && (
        <p className="text-sm text-slate-500">
          No jobs yet — trigger a sync on the Sources page, or wait for the next scheduled scrape.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {jobs.map((job, index) => {
          const match = matchQueries[index]?.data;
          return (
            <li key={job.id}>
              <Link
                to={`/jobs/${job.id}`}
                className="block rounded border border-slate-200 p-3 hover:border-slate-400"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{job.title}</span>
                  <div className="flex items-center gap-2">
                    {match && (
                      <span className="rounded-full bg-slate-900 px-2 py-0.5 text-xs text-white">
                        {match.practical_fit.toFixed(0)}%
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
          );
        })}
      </ul>
    </Card>
  );
}
