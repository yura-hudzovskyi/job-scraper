import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  flushRedis,
  getSystemStatus,
  getTaxonomyStatus,
  listUnmappedTerms,
  purgeQueue,
  resetData,
  resetPipelineConfig,
  reviewUnmappedTerm,
  runPipeline,
  testVoyage,
  updatePipelineConfig,
  type ResetTarget,
  type RunSteps,
} from "../api/endpoints";
import type {
  ConfigField,
  PipelineRun,
  PipelineStep,
  SystemStatus,
  UnmappedDecision,
} from "../api/types";
import {
  Badge,
  Button,
  Card,
  DangerButton,
  ErrorBanner,
  InfoBanner,
  Modal,
  SecondaryButton,
  SectionTitle,
  Stat,
  inputClass,
} from "../components/ui";

/** While a run is in progress the status is the interesting thing on the page,
 *  so it refreshes on its own instead of asking the user to reload. */
const RUNNING_POLL_MS = 3000;

const CONFIG_GROUPS: { title: string; blurb: string; fields: string[] }[] = [
  {
    title: "Models",
    blurb:
      "Both come from Voyage and use the one VOYAGE_API_KEY. Changing the embedding model invalidates every stored vector; the next run rebuilds them.",
    fields: ["embedding_model", "rerank_model"],
  },
  {
    title: "Scraping",
    blurb:
      "Each run scrapes one category per source — whichever has gone longest without one — so the rotation covers every category over time.",
    fields: ["scrape_enabled", "scrape_max_jobs_per_run"],
  },
  {
    title: "Matching",
    blurb:
      "Embedding search picks the candidate set; the reranker reads the top of it in full. The weight is how much the reranker's opinion counts against raw similarity.",
    fields: ["retrieval_limit", "rerank_top_k", "rerank_weight"],
  },
  {
    title: "Recommendation bands",
    blurb:
      "Applied to the final 0-100 score. Anything below the consider threshold is hidden from the jobs list by default.",
    fields: ["apply_threshold", "consider_threshold"],
  },
  {
    title: "Retention",
    blurb: "Runs once a day and deletes vacancies that have stopped appearing in scrapes.",
    fields: ["job_retention_days"],
  },
];

const RESETS: { target: ResetTarget; label: string; blurb: string; danger?: boolean }[] = [
  {
    target: "notifications",
    label: "Clear notification history",
    blurb:
      "Deletes delivery records only. Matches stay, so anything still above your notification threshold gets delivered again on the next run.",
  },
  {
    target: "matches",
    label: "Clear matches",
    blurb:
      "Deletes every match and its notifications. Vacancies and their vectors survive, so rebuilding costs one rerank pass — no re-scrape, no re-embed.",
  },
  {
    target: "embeddings",
    label: "Clear embeddings",
    blurb:
      "Deletes every vector. The next run re-embeds the whole corpus. Use this after changing the embedding model if you want a clean index.",
  },
  {
    target: "jobs",
    label: "Clear vacancies",
    blurb:
      "Deletes every vacancy and everything that only exists because of one: matches, notifications, vectors and scrape history.",
    danger: true,
  },
  {
    target: "all",
    label: "Reset everything",
    blurb:
      "Vacancies, vectors, matches, notifications, run history, and the queued task backlog. Keeps your account: login, CVs, preferences, Telegram connection and the settings above.",
    danger: true,
  },
];

function formatSeconds(seconds: number) {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

function formatDuration(from: string, to: string | null) {
  const end = to ? new Date(to).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(from).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** One step's counts, rendered from whatever the backend recorded rather than a
 *  fixed list of keys — a step that starts reporting something new shows up here
 *  without a frontend change. */
function StepRow({ step }: { step: PipelineStep }) {
  const { name, status, reason, ...rest } = step;
  const detail = Object.entries(rest)
    .filter(([key, value]) => key !== "results" && key !== "sources" && value !== null && value !== false)
    .map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value)}`)
    .join(" · ");

  return (
    <li className="flex flex-wrap items-baseline gap-2 text-xs">
      <span className="w-16 font-medium text-slate-700">{name}</span>
      <Badge tone={status === "ok" ? "ok" : status === "skipped" ? "neutral" : "warn"}>
        {status ?? "?"}
      </Badge>
      {reason && <span className="text-slate-500">{reason}</span>}
      {detail && <span className="text-slate-500">{detail}</span>}
    </li>
  );
}

function RunRow({ run }: { run: PipelineRun }) {
  const tone = run.status === "succeeded" ? "ok" : run.status === "failed" ? "bad" : "warn";
  return (
    <li className="rounded border border-slate-200 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium">
          {run.trigger} · {new Date(run.started_at).toLocaleString()}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">
            {formatDuration(run.started_at, run.finished_at)}
          </span>
          <Badge tone={tone}>{run.status}</Badge>
        </div>
      </div>
      {run.steps.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {run.steps.map((step, index) => (
            <StepRow key={`${step.name}-${index}`} step={step} />
          ))}
        </ul>
      )}
      {run.error && <p className="mt-2 text-xs text-red-700">{run.error}</p>}
    </li>
  );
}

function ConfigInput({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: string | number | boolean;
  onChange: (value: string | number | boolean) => void;
}) {
  if (field.type === "bool") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="text-slate-700">{value ? "On" : "Off"}</span>
      </label>
    );
  }
  if (field.type === "str") {
    return (
      <input
        className={inputClass}
        value={String(value)}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  return (
    <input
      type="number"
      className={inputClass}
      value={String(value)}
      min={field.minimum ?? undefined}
      max={field.maximum ?? undefined}
      step={field.type === "float" ? 0.05 : 1}
      onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
    />
  );
}

function PipelineDiagram({ status }: { status: SystemStatus }) {
  const { embeddings, config } = status;
  const configured = Object.fromEntries(config.fields.map((field) => [field.name, field.value]));
  const stages = [
    {
      label: "1 · Scrape",
      detail: `DOU + Djinni, one category each per run, up to ${configured.scrape_max_jobs_per_run} listings`,
      value: `${status.counts.canonical_jobs ?? 0} vacancies`,
    },
    {
      label: "2 · Embed",
      detail: `Every vacancy becomes one vector with ${embeddings.model}`,
      value: `${embeddings.jobs_embedded}/${embeddings.jobs_total} embedded`,
    },
    {
      label: "3 · Search",
      detail: `Your CV becomes a vector too; cosine similarity keeps the top ${configured.retrieval_limit}`,
      value: `${embeddings.profiles_embedded} CV vector(s)`,
    },
    {
      label: "4 · Filter",
      detail: "Your own rules — blocked stack, salary floor, locations, blacklists — remove vacancies",
      value: "deterministic",
    },
    {
      label: "5 · Rerank",
      detail: `${configured.rerank_model} reads your CV and the top ${configured.rerank_top_k} vacancies together`,
      value: `weight ${configured.rerank_weight}`,
    },
    {
      label: "6 · Notify",
      detail: "Matches above your notification threshold go to Telegram",
      value: status.telegram_configured ? "Telegram ready" : "no bot configured",
    },
  ];

  return (
    <ol className="flex flex-col gap-1.5">
      {stages.map((stage) => (
        <li
          key={stage.label}
          className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded border border-slate-200 px-3 py-2"
        >
          <span className="w-24 text-sm font-medium">{stage.label}</span>
          <span className="flex-1 text-xs text-slate-600">{stage.detail}</span>
          <span className="text-xs font-medium text-slate-500 tabular-nums">{stage.value}</span>
        </li>
      ))}
    </ol>
  );
}

/** The taxonomy the linker matches against, and the terms it could not cover.
 *
 *  Its own component with its own queries: the taxonomy is imported by a worker
 *  task on a completely different schedule from the pipeline, so folding it into
 *  the system status would make one screen's refresh depend on the other's.
 */
function TaxonomyPanel() {
  const queryClient = useQueryClient();
  const taxonomyQuery = useQuery({ queryKey: ["taxonomy"], queryFn: getTaxonomyStatus });
  const unmappedQuery = useQuery({
    queryKey: ["taxonomy-unmapped"],
    queryFn: () => listUnmappedTerms(25),
  });

  // Decisions made since the list was loaded, kept so the row can stay where it
  // is instead of vanishing. Reviewing is a column of near-identical rows and a
  // fast hand: a list that reorders itself between two clicks puts a different
  // term under the second one. Rows leave the queue on the next refresh, when
  // the user is looking at the list rather than at the button.
  const [decided, setDecided] = useState<Record<string, UnmappedDecision>>({});

  const reviewMutation = useMutation({
    mutationFn: ({ term, decision }: { term: string; decision: UnmappedDecision }) =>
      reviewUnmappedTerm(term, decision),
    onSuccess: (_result, { term, decision }) => {
      setDecided((current) => {
        if (decision !== "pending") return { ...current, [term]: decision };
        const { [term]: _removed, ...rest } = current;
        return rest;
      });
      queryClient.invalidateQueries({ queryKey: ["taxonomy"] });
    },
  });

  const taxonomy = taxonomyQuery.data;
  const unmapped = unmappedQuery.data ?? [];

  return (
    <Card>
      <SectionTitle>Taxonomy</SectionTitle>
      {taxonomyQuery.isLoading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : !taxonomy ? (
        <InfoBanner tone="warn">
          No taxonomy imported. Skills in a vacancy are matched as raw text until a release is
          imported with the <code>taxonomy.import_release</code> task.
        </InfoBanner>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat value={taxonomy.concepts} label="concepts" />
            <Stat value={taxonomy.relations} label="relations" />
            <Stat value={taxonomy.languages.length} label="languages" />
            <Stat value={taxonomy.pending_unmapped} label="unreviewed terms" />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Badge tone={taxonomy.status === "active" ? "ok" : "neutral"}>
              {taxonomy.namespace} {taxonomy.version} · {taxonomy.status}
            </Badge>
            {taxonomy.languages.map((language) => (
              <Badge key={language}>{language}</Badge>
            ))}
            {taxonomy.source_checksum && (
              <Badge title="sha256 of the release archive that was imported">
                {taxonomy.source_checksum.slice(0, 12)}
              </Badge>
            )}
          </div>
        </>
      )}

      <h3 className="mt-6 mb-1 text-sm font-medium text-slate-800">Terms the taxonomy missed</h3>
      <p className="mb-3 text-xs text-slate-600">
        Words the linker read as a skill but found no concept for, commonest first. Seen once is
        usually a typo; seen hundreds of times is a real gap. Nothing here affects matching — a
        decision is recorded for a person to act on, never applied automatically, and every one
        can be taken back.
      </p>
      {unmappedQuery.isLoading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : unmapped.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing waiting for review.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {unmapped.map((term) => {
            const decision = decided[term.normalized_text];
            return (
              <li
                key={term.normalized_text}
                className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded border px-3 py-2 ${
                  decision ? "border-slate-100 bg-slate-50 text-slate-400" : "border-slate-200"
                }`}
              >
                <span className="flex-1 text-sm" title={`seen as "${term.sample_raw_text}"`}>
                  {term.sample_raw_text}
                </span>
                <span className="text-xs font-medium tabular-nums">×{term.occurrences}</span>
                {decision ? (
                  <>
                    <span className="text-xs font-medium">
                      {decision === "promoted" ? "marked worth adding" : "ignored"}
                    </span>
                    <SecondaryButton
                      onClick={() =>
                        reviewMutation.mutate({
                          term: term.normalized_text,
                          decision: "pending",
                        })
                      }
                    >
                      Undo
                    </SecondaryButton>
                  </>
                ) : (
                  <>
                    {/* Ignore first and plainest: it is the answer for most of
                        this list, and the one with no consequences. */}
                    <SecondaryButton
                      onClick={() =>
                        reviewMutation.mutate({
                          term: term.normalized_text,
                          decision: "ignored",
                        })
                      }
                    >
                      Ignore
                    </SecondaryButton>
                    <Button
                      onClick={() =>
                        reviewMutation.mutate({
                          term: term.normalized_text,
                          decision: "promoted",
                        })
                      }
                    >
                      Worth adding
                    </Button>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {reviewMutation.isError && (
        <div className="mt-2">
          <ErrorBanner message="Could not record that decision — see the server logs" />
        </div>
      )}
    </Card>
  );
}


export function System() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
    refetchInterval: (query) => (query.state.data?.active_run ? RUNNING_POLL_MS : false),
  });
  const status = statusQuery.data;

  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({});
  useEffect(() => {
    if (status) {
      setDraft(Object.fromEntries(status.config.fields.map((field) => [field.name, field.value])));
    }
  }, [status?.config]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["system-status"] });

  const saveMutation = useMutation({
    mutationFn: (values: Record<string, string | number | boolean>) => updatePipelineConfig(values),
    onSuccess: invalidate,
  });
  const resetConfigMutation = useMutation({ mutationFn: resetPipelineConfig, onSuccess: invalidate });
  const testMutation = useMutation({ mutationFn: testVoyage });
  const runMutation = useMutation({
    mutationFn: (steps: RunSteps) => runPipeline(steps),
    onSuccess: invalidate,
  });
  const purgeMutation = useMutation({ mutationFn: purgeQueue, onSuccess: invalidate });
  const redisMutation = useMutation({ mutationFn: flushRedis });

  const [pendingReset, setPendingReset] = useState<ResetTarget | null>(null);
  const resetMutation = useMutation({
    mutationFn: (target: ResetTarget) => resetData(target),
    onSuccess: () => {
      setPendingReset(null);
      invalidate();
    },
  });

  const fieldsByName = Object.fromEntries((status?.config.fields ?? []).map((f) => [f.name, f]));
  const changed = Object.entries(draft).filter(
    ([name, value]) => fieldsByName[name] && fieldsByName[name].value !== value,
  );
  const running = Boolean(status?.active_run);

  if (statusQuery.isLoading) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }
  if (statusQuery.isError || !status) {
    return <ErrorBanner message="Failed to load the system status" />;
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Pipeline</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Six steps, no AI beyond two Voyage calls: one turns text into vectors, the other reads
          your CV and a vacancy together and scores the fit. Everything else — filters, thresholds,
          dedup — is deterministic and configurable below.
        </p>
        <PipelineDiagram status={status} />

        {status.blockers.length > 0 && (
          <div className="mt-4">
            <InfoBanner tone="warn">
              <p className="mb-1 font-medium">A run won't produce matches yet:</p>
              <ul className="list-inside list-disc">
                {status.blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </InfoBanner>
          </div>
        )}
        {status.embeddings.stale_vectors > 0 && (
          <div className="mt-3">
            <InfoBanner tone="warn">
              {status.embeddings.stale_vectors} vector(s) were built with a different model and
              can't be compared against the current one. The next run re-embeds them; matching stays
              thin until it has.
            </InfoBanner>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            onClick={() => runMutation.mutate("full")}
            disabled={running || runMutation.isPending}
          >
            {running ? "Running…" : "Run the whole pipeline"}
          </Button>
          <SecondaryButton
            onClick={() => runMutation.mutate("match")}
            disabled={running || runMutation.isPending}
            title="Embed and re-match what is already in the database, without scraping"
          >
            Re-match only
          </SecondaryButton>
          <SecondaryButton
            onClick={() => runMutation.mutate("scrape")}
            disabled={running || runMutation.isPending}
            title="Fetch new vacancies without re-matching"
          >
            Scrape only
          </SecondaryButton>
          <span className="text-xs text-slate-500">
            Also runs automatically every {formatSeconds(status.scrape_interval_seconds)}.
          </span>
        </div>
        {runMutation.isError && (
          <div className="mt-3">
            <ErrorBanner
              message={
                runMutation.error instanceof ApiError
                  ? runMutation.error.message
                  : "Failed to start the pipeline"
              }
            />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>At a glance</SectionTitle>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat value={status.counts.canonical_jobs ?? 0} label="vacancies" />
          <Stat value={status.counts.raw_jobs ?? 0} label="raw scrapes" />
          <Stat value={status.embeddings.jobs_embedded} label="embedded" />
          <Stat value={status.counts.matches ?? 0} label="matches" />
          <Stat value={status.counts.notifications ?? 0} label="notifications" />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Badge tone={status.voyage_configured ? "ok" : "bad"}>
            Voyage API key {status.voyage_configured ? "set" : "missing"}
          </Badge>
          <Badge tone={status.telegram_configured ? "ok" : "neutral"}>
            Telegram bot {status.telegram_configured ? "configured" : "not configured"}
          </Badge>
          {Object.entries(status.sources).map(([source, count]) => (
            <Badge key={source} title={`${status.categories[source]?.length ?? 0} categories`}>
              {source}: {count} raw
            </Badge>
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Both API keys and the database URL are read from .env at startup and are deliberately not
          editable here — they're deployment secrets, not settings.
        </p>
      </Card>

      <TaxonomyPanel />

      <Card>
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <SectionTitle>Configuration</SectionTitle>
          <div className="mb-3 flex gap-2">
            <SecondaryButton
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending}
              title="Make one real call against each configured model"
            >
              {testMutation.isPending ? "Testing…" : "Test models"}
            </SecondaryButton>
            <SecondaryButton
              onClick={() => resetConfigMutation.mutate()}
              disabled={resetConfigMutation.isPending}
            >
              Reset to defaults
            </SecondaryButton>
          </div>
        </div>
        <p className="mb-4 text-sm text-slate-600">
          Every number the pipeline runs on. Saved to the database and picked up by the next run —
          no redeploy, no restart.
        </p>

        {testMutation.isSuccess && (
          <div className="mb-4">
            <InfoBanner tone={testMutation.data.rerank_ok ? "ok" : "warn"}>
              {testMutation.data.error ? (
                <>Model test failed — {testMutation.data.error}</>
              ) : (
                <>
                  Both models answered. Embeddings are {testMutation.data.embedding_dimension}
                  -dimensional; reranking works.
                </>
              )}
            </InfoBanner>
          </div>
        )}

        {CONFIG_GROUPS.map((group) => (
          <div key={group.title} className="mb-6 last:mb-0">
            <p className="text-xs font-semibold tracking-wide text-slate-400 uppercase">
              {group.title}
            </p>
            <p className="mt-1 mb-3 text-xs text-slate-500">{group.blurb}</p>
            <div className="flex flex-col gap-4">
              {group.fields.map((name) => {
                const field = fieldsByName[name];
                if (!field) return null;
                const isChanged = draft[name] !== field.value;
                return (
                  <div key={name} className="grid gap-1 sm:grid-cols-[16rem_1fr] sm:gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{name.replace(/_/g, " ")}</span>
                        {isChanged && <Badge tone="warn">unsaved</Badge>}
                      </div>
                      <ConfigInput
                        field={field}
                        value={draft[name] ?? field.value}
                        onChange={(value) => setDraft({ ...draft, [name]: value })}
                      />
                    </div>
                    <div className="text-xs text-slate-500">
                      <p>{field.description}</p>
                      <p className="mt-1 text-slate-400">
                        Default: {String(field.default)}
                        {field.minimum !== null && field.maximum !== null && (
                          <> · allowed {field.minimum}–{field.maximum}</>
                        )}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        <div className="mt-4 flex items-center gap-3">
          <Button
            onClick={() => saveMutation.mutate(Object.fromEntries(changed))}
            disabled={changed.length === 0 || saveMutation.isPending}
          >
            {saveMutation.isPending ? "Saving…" : `Save ${changed.length || ""} change(s)`}
          </Button>
          {saveMutation.isSuccess && changed.length === 0 && (
            <span className="text-sm text-green-700">Saved.</span>
          )}
        </div>
        {saveMutation.isError && (
          <div className="mt-2">
            <ErrorBanner
              message={
                saveMutation.error instanceof ApiError
                  ? saveMutation.error.message
                  : "Failed to save the configuration"
              }
            />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Runs</SectionTitle>
        <p className="mb-3 text-sm text-slate-600">
          Every run records what each step actually did. A run that produced nothing still says why.
        </p>
        {status.active_run && (
          <div className="mb-3">
            <InfoBanner>
              A run started {new Date(status.active_run.started_at).toLocaleTimeString()} is in
              progress — this page refreshes itself until it finishes.
            </InfoBanner>
          </div>
        )}
        {status.recent_runs.length === 0 ? (
          <p className="text-sm text-slate-500">No runs recorded yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {status.recent_runs.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <SectionTitle>Start over</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Each button deletes exactly what it names and reports the row counts. None of them touch
          your login, CVs, preferences or Telegram connection.
        </p>
        <div className="flex flex-col gap-3">
          {RESETS.map((reset) => (
            <div
              key={reset.target}
              className="flex flex-wrap items-start justify-between gap-3 rounded border border-slate-200 p-3"
            >
              <p className="flex-1 text-xs text-slate-600">
                <span className="mb-0.5 block text-sm font-medium text-slate-800">
                  {reset.label}
                </span>
                {reset.blurb}
              </p>
              {reset.danger ? (
                <DangerButton onClick={() => setPendingReset(reset.target)}>Delete</DangerButton>
              ) : (
                <SecondaryButton onClick={() => setPendingReset(reset.target)}>
                  Delete
                </SecondaryButton>
              )}
            </div>
          ))}
        </div>

        <div className="mt-4 flex flex-wrap gap-3">
          <SecondaryButton onClick={() => purgeMutation.mutate()} disabled={purgeMutation.isPending}>
            Purge the task queue
          </SecondaryButton>
          <SecondaryButton onClick={() => redisMutation.mutate()} disabled={redisMutation.isPending}>
            Clear Redis
          </SecondaryButton>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          The queue holds tasks no worker has started yet — purging is the fix for a backlog stuck
          behind a bad run. Redis holds only the queue and task results; nothing there is a system
          of record.
        </p>
        {(purgeMutation.isSuccess || redisMutation.isSuccess || resetMutation.isSuccess) && (
          <div className="mt-3">
            <InfoBanner tone="ok">
              Deleted:{" "}
              {Object.entries({
                ...(resetMutation.data?.deleted ?? {}),
                ...(purgeMutation.data?.deleted ?? {}),
                ...(redisMutation.data?.deleted ?? {}),
              })
                .map(([table, count]) => `${count} ${table.replace(/_/g, " ")}`)
                .join(", ")}
            </InfoBanner>
          </div>
        )}
      </Card>

      {pendingReset && (
        <Modal title={RESETS.find((r) => r.target === pendingReset)!.label} onClose={() => setPendingReset(null)}>
          <p className="mb-4 text-sm text-slate-600">
            {RESETS.find((r) => r.target === pendingReset)!.blurb}
          </p>
          <p className="mb-4 text-sm text-slate-600">This cannot be undone.</p>
          <div className="flex justify-end gap-2">
            <SecondaryButton onClick={() => setPendingReset(null)} disabled={resetMutation.isPending}>
              Cancel
            </SecondaryButton>
            <DangerButton
              onClick={() => resetMutation.mutate(pendingReset)}
              disabled={resetMutation.isPending}
            >
              {resetMutation.isPending ? "Deleting…" : "Delete"}
            </DangerButton>
          </div>
          {resetMutation.isError && (
            <div className="mt-2">
              <ErrorBanner message="Failed — see the server logs" />
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
