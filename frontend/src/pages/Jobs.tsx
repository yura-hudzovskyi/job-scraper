import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { listJobs, rematch } from "../api/endpoints";
import type { JobMatch } from "../api/types";
import {
  Badge,
  Card,
  ErrorBanner,
  InfoBanner,
  SecondaryButton,
  SectionTitle,
} from "../components/ui";

const PAGE_SIZE = 25;

const RECOMMENDATION_TONES = {
  apply: "ok",
  consider: "warn",
  skip: "neutral",
} as const;

/** The score, and immediately next to it the two numbers it was computed from.
 *  A list that shows only a percentage teaches people to trust it; this one
 *  keeps the arithmetic in view. */
function MatchBadges({ match }: { match: JobMatch }) {
  if (!match.eligible) {
    return (
      <Badge tone="bad" title={match.filter_reasons.join("; ")}>
        filtered out
      </Badge>
    );
  }
  const tone = RECOMMENDATION_TONES[match.recommendation as keyof typeof RECOMMENDATION_TONES];
  return (
    <>
      <span className="rounded-full bg-slate-900 px-2 py-0.5 text-xs text-white tabular-nums">
        {match.score.toFixed(0)}
      </span>
      <Badge tone={tone ?? "neutral"}>{match.recommendation}</Badge>
      <Badge
        title={
          match.relevance === null
            ? "Outside the rerank top-K — scored on embedding similarity alone"
            : `similarity ${(match.similarity * 100).toFixed(0)}% · rerank ${(match.relevance * 100).toFixed(0)}%`
        }
      >
        {match.relevance === null
          ? `sim ${(match.similarity * 100).toFixed(0)}%`
          : `sim ${(match.similarity * 100).toFixed(0)}% · rr ${(match.relevance * 100).toFixed(0)}%`}
      </Badge>
    </>
  );
}

export function Jobs() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [includeSkipped, setIncludeSkipped] = useState(false);

  const jobsQuery = useQuery({
    queryKey: ["jobs", offset, includeSkipped],
    queryFn: () => listJobs(PAGE_SIZE, offset, includeSkipped),
    placeholderData: (previous) => previous,
  });
  const jobs = jobsQuery.data?.items ?? [];
  const total = jobsQuery.data?.total ?? 0;

  const rematchMutation = useMutation({
    mutationFn: rematch,
    onSuccess: () => {
      // Matching is a background pass; give it a moment before reloading.
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["jobs"] }), 4000);
    },
  });

  const unmatched = jobs.filter((job) => job.match === null).length;

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Jobs</SectionTitle>
        <SecondaryButton
          onClick={() => rematchMutation.mutate()}
          disabled={rematchMutation.isPending}
          title="Re-run embedding search and reranking against the vacancies already stored"
        >
          {rematchMutation.isPending ? "Queuing…" : "Re-match"}
        </SecondaryButton>
      </div>

      <p className="mb-3 text-sm text-slate-600">
        Ranked by embedding similarity to your CV, then reranked at the top. Each row shows the
        score and the two signals behind it — <code>sim</code> is cosine similarity,{" "}
        <code>rr</code> is the reranker's relevance.
      </p>

      {rematchMutation.isSuccess && (
        <div className="mb-3">
          <InfoBanner tone="ok">
            Queued — matching runs in the background and this list refreshes shortly.
          </InfoBanner>
        </div>
      )}

      <label className="mb-3 flex w-fit items-center gap-2 text-sm text-slate-600">
        <input
          type="checkbox"
          checked={includeSkipped}
          onChange={(event) => {
            setIncludeSkipped(event.target.checked);
            setOffset(0);
          }}
        />
        Show everything, including low scores and filtered-out vacancies
      </label>

      {jobsQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {jobsQuery.isError && <ErrorBanner message="Failed to load jobs" />}
      {jobs.length === 0 && !jobsQuery.isLoading && (
        <p className="text-sm text-slate-500">
          Nothing here yet — run the pipeline from the System page to scrape, embed and match.
        </p>
      )}
      {unmatched > 0 && (
        <div className="mb-3">
          <InfoBanner>
            {unmatched} vacancy(ies) on this page have no match yet — they were scraped but fell
            outside the retrieval limit, or matching hasn't run since.
          </InfoBanner>
        </div>
      )}

      <ul className="flex flex-col gap-2">
        {jobs.map((job) => (
          <li key={job.id}>
            <Link
              to={`/jobs/${job.id}`}
              className="block rounded border border-slate-200 p-3 hover:border-slate-400"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{job.title}</span>
                <div className="flex flex-wrap items-center gap-2">
                  {job.match ? (
                    <MatchBadges match={job.match} />
                  ) : (
                    <Badge title="Scraped, but not scored for you yet">not matched</Badge>
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
            <SecondaryButton
              onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
              disabled={offset === 0}
            >
              Previous
            </SecondaryButton>
            <SecondaryButton
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total}
            >
              Next
            </SecondaryButton>
          </div>
        </div>
      )}
    </Card>
  );
}
