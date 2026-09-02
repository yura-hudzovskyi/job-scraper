import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { getJob, rematch } from "../api/endpoints";
import type { JobMatch } from "../api/types";
import {
  Badge,
  Card,
  ErrorBanner,
  InfoBanner,
  SecondaryButton,
  SectionTitle,
} from "../components/ui";

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

/** The score, spelled out as the sum it actually is. Someone who disagrees with a
 *  number should be able to see which half they disagree with. */
function ScoreBreakdown({ match }: { match: JobMatch }) {
  const weight = match.rerank_weight ?? 0;
  const rows: [string, string, string][] = [
    [
      "Embedding similarity",
      percent(match.similarity),
      `Cosine between your CV vector and this vacancy's, from ${match.embedding_model ?? "—"}`,
    ],
    match.relevance === null
      ? [
          "Reranker relevance",
          "not reranked",
          "This vacancy was outside the rerank top-K, so its score is the similarity alone",
        ]
      : [
          "Reranker relevance",
          percent(match.relevance),
          `${match.rerank_model} read your CV and this vacancy together${
            match.rerank_position ? ` — ranked #${match.rerank_position}` : ""
          }`,
        ],
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-4">
        <div>
          <p className="text-3xl font-bold tabular-nums">{match.score.toFixed(1)}</p>
          <p className="text-sm text-slate-500">score out of 100</p>
        </div>
        <Badge
          tone={
            match.recommendation === "apply"
              ? "ok"
              : match.recommendation === "consider"
                ? "warn"
                : "neutral"
          }
        >
          {match.recommendation}
        </Badge>
        {match.decision !== "pending" && <Badge>you: {match.decision}</Badge>}
      </div>

      <dl className="flex flex-col gap-2">
        {rows.map(([label, value, detail]) => (
          <div key={label} className="rounded border border-slate-200 px-3 py-2">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-sm text-slate-700">{label}</dt>
              <dd className="text-sm font-medium tabular-nums">{value}</dd>
            </div>
            <p className="mt-0.5 text-xs text-slate-500">{detail}</p>
          </div>
        ))}
      </dl>

      <p className="text-xs text-slate-500">
        {match.relevance === null ? (
          <>score = similarity × 100</>
        ) : (
          <>
            score = (similarity × {(1 - weight).toFixed(2)} + relevance × {weight.toFixed(2)}) × 100
          </>
        )}
        {match.scored_at && <> · computed {new Date(match.scored_at).toLocaleString()}</>}
      </p>
    </div>
  );
}

export function JobDetails() {
  const { jobId } = useParams<{ jobId: string }>();
  const queryClient = useQueryClient();

  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId!),
    enabled: Boolean(jobId),
  });

  const rematchMutation = useMutation({
    mutationFn: rematch,
    onSuccess: () => {
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["job", jobId] }), 4000);
    },
  });

  if (!jobId) return null;

  const job = jobQuery.data;
  const match = job?.match ?? null;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        {jobQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {jobQuery.isError && (
          <ErrorBanner
            message={
              jobQuery.error instanceof ApiError ? jobQuery.error.message : "Failed to load the job"
            }
          />
        )}
        {job && (
          <>
            <SectionTitle>{job.title}</SectionTitle>
            <p className="mb-3 text-sm text-slate-600">
              {job.company}
              {job.source_count > 1 && ` · seen on ${job.source_count} sources`}
            </p>
            <p className="text-sm whitespace-pre-line text-slate-700">{job.description}</p>
          </>
        )}
      </Card>

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <SectionTitle>Match</SectionTitle>
          <SecondaryButton
            onClick={() => rematchMutation.mutate()}
            disabled={rematchMutation.isPending}
          >
            {rematchMutation.isPending ? "Queuing…" : "Re-match"}
          </SecondaryButton>
        </div>

        {match === null && (
          <InfoBanner>
            Not matched yet. Matching needs an uploaded CV and at least one embedded vacancy — run
            the pipeline from the System page, or press Re-match if both are already in place.
          </InfoBanner>
        )}

        {match && !match.eligible && (
          <div className="flex flex-col gap-3">
            <InfoBanner tone="warn">
              <p className="mb-1 font-medium">
                Filtered out by your own rules, before any scoring:
              </p>
              <ul className="list-inside list-disc">
                {match.filter_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </InfoBanner>
            <p className="text-xs text-slate-500">
              Embedding similarity was {percent(match.similarity)}. Filters run before the reranker,
              so no rerank call was spent on this vacancy. Change the rules on the Settings page.
            </p>
          </div>
        )}

        {match && match.eligible && <ScoreBreakdown match={match} />}

        {rematchMutation.isSuccess && (
          <p className="mt-3 text-sm text-green-700">
            Queued — this refreshes shortly once matching finishes.
          </p>
        )}
      </Card>

      {job && (
        <Card>
          <SectionTitle>What the models read</SectionTitle>
          <p className="mb-3 text-sm text-slate-600">
            The exact text sent to the embedding and rerank models for this vacancy — trimmed and
            labelled, but not summarised or rewritten. If a score looks wrong, this is where to
            look first.
          </p>
          <pre className="max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs whitespace-pre-wrap text-slate-700">
            {job.model_document}
          </pre>
        </Card>
      )}
    </div>
  );
}
