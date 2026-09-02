import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  getPipelineStatus,
  rebuildEmbeddings,
  runRetrieval,
  runScoring,
} from "../api/endpoints";
import type { PipelineStatus } from "../api/types";
import { Button, Card, ErrorBanner, Modal, SectionTitle } from "./ui";

// While something is running the numbers move on their own; polling keeps the
// buttons honest without the user reloading to find out.
const POLL_WHILE_RUNNING_MS = 5000;

type StepKey = "scoring" | "embeddings" | "retrieval";

type Step = {
  key: StepKey;
  title: string;
  what: string;
  /** Why it's this step's turn — the ordering isn't arbitrary. */
  why: string;
  confirm: string;
  action: () => Promise<unknown>;
  /** A reason string disables the button and is shown; null means it's ready. */
  blocked: (status: PipelineStatus) => string | null;
};

const STEPS: Step[] = [
  {
    key: "scoring",
    title: "1. Extract requirements and rescore every vacancy",
    what:
      "Reads each posting again for what it actually requires, then rescores it against your CV.",
    why:
      "Everything downstream compares against those requirements. A vacancy with none is scored on text similarity alone, which is what an “analysis level: limited” badge means.",
    confirm:
      "This re-reads every vacancy in the database with the LLM. It runs in the background and is bounded by the daily budget for job extraction — once that runs out the rest is read by the rules extractor and retried later. Existing scores are replaced as each job is re-read.",
    action: runScoring,
    blocked: (status) =>
      status.running.scoring
        ? "Already running — wait for the queue to drain."
        : status.jobs_total === 0
          ? "No vacancies in the database yet."
          : null,
  },
  {
    key: "embeddings",
    title: "2. Rebuild embeddings from scratch",
    what:
      "Deletes every stored vector, then re-indexes every vacancy and your CV, one lane at a time.",
    why:
      "A lane half-filled by a previous model, or marked ready when it only covers last month’s vacancies, is harder to trust than an empty one. All of it recomputes from the postings.",
    confirm:
      "This deletes every stored vector before rebuilding. Retrieval stops working until a lane covers the corpus again — minutes on the local model, longer on a hosted one. Nothing else is lost: vectors are derived data.",
    action: rebuildEmbeddings,
    blocked: (status) =>
      status.running.embeddings
        ? "Already running — watch lane coverage below."
        : status.jobs_total === 0
          ? "No vacancies to index yet."
          : null,
  },
  {
    key: "retrieval",
    title: "3. Search by embeddings and rerank",
    what:
      "Ranks the whole corpus against your CV inside one lane, reranks the shortlist with a model that reads both documents, and stores the relevance on each match.",
    why:
      "The next scoring run folds that relevance in as the role/domain signal, so this ordering ends up in the score instead of a separate list.",
    confirm:
      "This calls the rerank provider for up to a hundred vacancies. It replaces the relevance stored by any previous run; scores and LLM verdicts are untouched.",
    action: runRetrieval,
    blocked: (status) =>
      status.running.retrieval
        ? "Already running."
        : !status.has_profile
          ? "No analyzed CV yet — upload and analyze one on the Profile page."
          : !status.embeddings_ready
            ? "No embedding lane covers the corpus yet — run step 2 and wait for a lane to reach “ready”."
            : !status.profile_indexed
              ? "Your CV has no vectors yet — finish step 2."
              : null,
  },
];

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="text-sm text-slate-700">{value}</dd>
    </div>
  );
}

export function PipelineCard() {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Step | null>(null);

  const statusQuery = useQuery({
    queryKey: ["ai-pipeline"],
    queryFn: getPipelineStatus,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && Object.values(data.running).some(Boolean) ? POLL_WHILE_RUNNING_MS : false;
    },
  });

  const runMutation = useMutation({
    mutationFn: (step: Step) => step.action(),
    onSuccess: () => {
      setPending(null);
      queryClient.invalidateQueries({ queryKey: ["ai-pipeline"] });
    },
  });

  const status = statusQuery.data;

  return (
    <Card>
      <SectionTitle>Pipeline</SectionTitle>
      <p className="mb-3 text-sm text-slate-600">
        Three stages, in this order. Each one runs in the background over every vacancy in the
        database, and each refuses to start twice — a second press while one is running is
        rejected by the server, not just greyed out here.
      </p>

      {statusQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {statusQuery.isError && <ErrorBanner message="Failed to load pipeline status" />}

      {status && (
        <>
          <dl className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Vacancies" value={status.jobs_total} />
            <Stat label="Matches" value={status.matches_total} />
            <Stat label="Scored by the hybrid engine" value={status.matches_hybrid_scored} />
            <Stat label="With retrieval relevance" value={status.matches_with_relevance} />
            <Stat label="Reviewed by an LLM" value={status.matches_enriched} />
            <Stat
              label="Your CV"
              value={
                !status.has_profile
                  ? "not analyzed"
                  : status.profile_indexed
                    ? "indexed"
                    : "not indexed"
              }
            />
          </dl>

          {status.lanes.length > 0 && (
            <ul className="mb-4 flex flex-col gap-1 text-xs">
              {status.lanes.map((lane) => (
                <li key={lane.id} className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-600">{lane.id}</span>
                  <span className="text-slate-400">{lane.role}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 ${
                      lane.state === "ready"
                        ? "bg-slate-100 text-slate-600"
                        : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {lane.state}
                  </span>
                  <span className="text-slate-500">
                    {lane.jobs_covered}/{status.jobs_total} vacancies indexed
                  </span>
                </li>
              ))}
            </ul>
          )}

          <ol className="flex flex-col gap-4">
            {STEPS.map((step) => {
              const blocked = step.blocked(status);
              const running = status.running[step.key];
              return (
                <li key={step.key} className="rounded border border-slate-200 p-3">
                  <p className="text-sm font-medium">{step.title}</p>
                  <p className="mt-1 text-sm text-slate-600">{step.what}</p>
                  <p className="mt-1 text-xs text-slate-400">{step.why}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button
                      onClick={() => setPending(step)}
                      disabled={blocked !== null || runMutation.isPending}
                      title={blocked ?? undefined}
                      className={blocked ? "bg-slate-300 hover:bg-slate-300" : undefined}
                    >
                      {running ? "Running…" : "Run"}
                    </Button>
                    {blocked && <span className="text-xs text-amber-700">{blocked}</span>}
                  </div>
                </li>
              );
            })}
          </ol>

          {runMutation.isError && (
            <div className="mt-3">
              <ErrorBanner message="Failed to start — see server logs" />
            </div>
          )}

          <div className="mt-5 rounded border border-slate-200 bg-slate-50 p-3">
            <p className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
              Starting from a clean slate
            </p>
            <ol className="flex list-inside list-decimal flex-col gap-1 text-sm text-slate-600">
              <li>
                Analyze a CV on the Profile page. Nothing below means anything without one — it is
                the other half of every comparison.
              </li>
              <li>
                Run step 1 and let it finish. Watch “Scored by the hybrid engine” climb; if the
                daily extraction budget runs out, the rest is read by rules and retried
                automatically.
              </li>
              <li>
                Run step 2. Watch a lane reach <span className="font-medium">ready</span> above —
                until then retrieval refuses to run rather than search half an index.
              </li>
              <li>
                Run step 3, then run step 1 once more: the relevance it stored is folded into the
                score on the next pass.
              </li>
              <li>
                Leave the rest to the schedule. New vacancies are extracted, embedded and scored as
                they are scraped, and the daily enrichment pass reviews the matches where an LLM
                opinion could still change your decision.
              </li>
            </ol>
          </div>
        </>
      )}

      {pending && (
        <Modal title={pending.title.replace(/^\d+\.\s*/, "")} onClose={() => setPending(null)}>
          <p className="mb-3 text-sm text-slate-600">{pending.confirm}</p>
          <div className="flex justify-end gap-2">
            <Button
              className="bg-slate-600 hover:bg-slate-500"
              onClick={() => setPending(null)}
              disabled={runMutation.isPending}
            >
              Cancel
            </Button>
            <Button onClick={() => runMutation.mutate(pending)} disabled={runMutation.isPending}>
              {runMutation.isPending ? "Starting…" : "Run it"}
            </Button>
          </div>
        </Modal>
      )}
    </Card>
  );
}
