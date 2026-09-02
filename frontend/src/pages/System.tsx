import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  flushRedis,
  getAiModels,
  getAiUsage,
  purgeCelery,
  testAiModel,
  updateAiModels,
} from "../api/endpoints";
import type {
  AiModelField,
  AiModelsUpdateRequest,
  CapabilityStatus,
  LaneStatus,
} from "../api/types";
import { Button, Card, ErrorBanner, Modal, SectionTitle, inputClass } from "../components/ui";

type ModelFieldKey = keyof AiModelsUpdateRequest;

const CAPABILITY_LABELS: Record<string, string> = {
  profile_extraction: "CV analysis & preferences AI-fill",
  job_extraction: "Job requirement extraction",
  match_enrichment: '"Should I apply?" verdicts',
};

const REASON_LABELS: Record<string, string> = {
  rate_limit: "rate limited",
  quota_exhausted: "quota exhausted",
  transient: "provider error",
  fatal: "misconfigured — check the key and model id",
  schema: "unusable response",
};

function waitLabel(seconds: number | null) {
  if (seconds === null) return "";
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

/** What the router is doing right now: which legs it can use, why it can't use
 *  the others, and how much of today's budget each capability has spent. Every
 *  failure path in this app degrades quietly by design, so without this "the AI
 *  stopped working" looks exactly like "nothing needed the AI". */
function CapabilityCard({ status }: { status: CapabilityStatus }) {
  const spent = status.budget_limit > 0 ? status.budget_used / status.budget_limit : 0;

  return (
    <div className="rounded border border-slate-200 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium">
          {CAPABILITY_LABELS[status.capability] ?? status.capability}
        </span>
        <span className={`text-xs ${spent >= 1 ? "text-amber-700" : "text-slate-500"}`}>
          {status.budget_used} / {status.budget_limit} calls today
        </span>
      </div>
      {status.legs.length === 0 ? (
        <p className="mt-1 text-xs text-slate-400">No provider configured — this runs without AI.</p>
      ) : (
        <ol className="mt-2 flex flex-col gap-1 text-xs">
          {status.legs.map((leg, index) => (
            <li key={`${leg.provider}:${leg.model}`} className="flex items-center gap-2">
              <span className="text-slate-400">{index + 1}.</span>
              <span className="text-slate-600">
                {leg.provider} · {leg.model}
              </span>
              <Badge
                open={!leg.available}
                label={
                  leg.available
                    ? "available"
                    : `${REASON_LABELS[leg.reason ?? ""] ?? leg.reason ?? "unavailable"}` +
                      (leg.retry_after_seconds !== null
                        ? ` · back in ${waitLabel(leg.retry_after_seconds)}`
                        : "")
                }
              />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** Embedding lanes and how far each one has got. Coverage is the number that
 *  matters: a lane below the readiness threshold is skipped by retrieval
 *  entirely, because a half-built lane quietly returns a smaller world. */
function LaneRow({ lane }: { lane: LaneStatus }) {
  const percent = lane.jobs_total > 0 ? Math.floor((lane.jobs_covered / lane.jobs_total) * 100) : 0;

  return (
    <li className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-slate-600">
        {lane.provider} · {lane.model}
      </span>
      <span className="text-slate-400">
        {lane.role} · {lane.dimension}d
      </span>
      <Badge open={lane.state !== "ready"} label={lane.state} />
      <span className="text-slate-500">
        {lane.jobs_covered}/{lane.jobs_total} jobs ({percent}%)
      </span>
    </li>
  );
}

function Badge({ open, label }: { open: boolean; label: string }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
        open ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600"
      }`}
    >
      {label}
    </span>
  );
}

export function System() {
  const queryClient = useQueryClient();
  const modelsQuery = useQuery({ queryKey: ["ai-models"], queryFn: getAiModels });
  // Budgets say what is left today; the ledger says where it went and how much
  // of it failed — the two answer different questions.
  const usageQuery = useQuery({ queryKey: ["ai-usage"], queryFn: () => getAiUsage(24) });

  const [inputs, setInputs] = useState<Record<ModelFieldKey, string>>({
    groq_model: "",
    gemini_model: "",
  });

  useEffect(() => {
    if (modelsQuery.data) {
      setInputs({
        groq_model: modelsQuery.data.groq_model.value,
        gemini_model: modelsQuery.data.gemini_model.value,
      });
    }
  }, [modelsQuery.data]);

  const updateMutation = useMutation({
    mutationFn: (payload: AiModelsUpdateRequest) => updateAiModels(payload),
    onSuccess: (data) => queryClient.setQueryData(["ai-models"], data),
  });

  const groqTestMutation = useMutation({
    mutationFn: () => testAiModel("groq", inputs.groq_model),
  });
  const geminiTestMutation = useMutation({
    mutationFn: () => testAiModel("gemini", inputs.gemini_model),
  });

  const [isFlushOpen, setIsFlushOpen] = useState(false);
  const flushMutation = useMutation({
    mutationFn: flushRedis,
    onSuccess: () => {
      setIsFlushOpen(false);
      queryClient.invalidateQueries({ queryKey: ["ai-models"] });
    },
  });

  const [isPurgeOpen, setIsPurgeOpen] = useState(false);
  const purgeMutation = useMutation({
    mutationFn: purgeCelery,
    onSuccess: () => setIsPurgeOpen(false),
  });

  function renderModelRow(
    key: ModelFieldKey,
    label: string,
    field: AiModelField | undefined,
    options: { testTier?: "groq" | "gemini"; configured?: boolean } = {},
  ) {
    const testMutation = options.testTier === "groq" ? groqTestMutation : geminiTestMutation;
    const dirty = field !== undefined && inputs[key] !== field.value;

    return (
      <div className="border-b border-slate-100 py-4 last:border-b-0">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          {field && (
            <Badge open={field.is_override} label={field.is_override ? "override" : "default"} />
          )}
          {options.configured === false && (
            <span className="text-xs text-slate-400">(no API key set — not used)</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input
            className={inputClass}
            value={inputs[key]}
            onChange={(e) => setInputs({ ...inputs, [key]: e.target.value })}
            placeholder={field?.default}
          />
          <Button
            className="bg-slate-600 hover:bg-slate-500 whitespace-nowrap"
            disabled={!dirty || updateMutation.isPending}
            onClick={() => updateMutation.mutate({ [key]: inputs[key] || null })}
          >
            Save
          </Button>
          {field?.is_override && (
            <Button
              className="bg-slate-600 hover:bg-slate-500 whitespace-nowrap"
              disabled={updateMutation.isPending}
              onClick={() => updateMutation.mutate({ [key]: null })}
            >
              Reset to default
            </Button>
          )}
          {options.testTier && (
            <Button
              className="whitespace-nowrap bg-slate-600 hover:bg-slate-500"
              disabled={testMutation.isPending || !inputs[key]}
              onClick={() => testMutation.mutate()}
            >
              {testMutation.isPending ? "Testing…" : "Test"}
            </Button>
          )}
        </div>
        {field && (
          <p className="mt-1 text-xs text-slate-400">.env default: {field.default}</p>
        )}
        {testMutation.isSuccess && options.testTier && (
          <p
            className={`mt-1 text-xs ${
              testMutation.data.ok ? "text-green-700" : "text-red-700"
            }`}
          >
            {testMutation.data.ok
              ? `Works — responded as ${testMutation.data.model_label}`
              : `Failed: ${testMutation.data.error}`}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>AI models</SectionTitle>
        <p className="mb-2 text-sm text-slate-600">
          Each capability tries its providers in order, skipping any the router has parked after a
          failure, and stops for the day when its own budget runs out — one budget per capability,
          so background work can't spend what interactive work needs. Model changes take effect on
          the very next call, no redeploy needed. See docs/matching-engine.md.
        </p>
        {modelsQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {modelsQuery.isError && <ErrorBanner message="Failed to load AI model config" />}
        {modelsQuery.data && (
          <div>
            <div className="mb-4 flex flex-col gap-2">
              {modelsQuery.data.capabilities.map((capability) => (
                <CapabilityCard key={capability.capability} status={capability} />
              ))}
            </div>
            {modelsQuery.data.lanes.length > 0 && (
              <div className="mb-4 rounded border border-slate-200 p-3">
                <p className="mb-2 text-xs font-semibold tracking-wide text-slate-400 uppercase">
                  Embedding lanes
                </p>
                <ul className="flex flex-col gap-1">
                  {modelsQuery.data.lanes.map((lane) => (
                    <LaneRow key={lane.id} lane={lane} />
                  ))}
                </ul>
                <p className="mt-2 text-xs text-slate-400">
                  Retrieval uses the best lane that covers the corpus and never mixes two — vectors
                  from different models aren't comparable.
                </p>
              </div>
            )}
            <p className="mb-3 text-xs font-semibold tracking-wide text-slate-400 uppercase">
              Job pipeline model
            </p>
            {renderModelRow("groq_model", "Groq model", modelsQuery.data.groq_model, {
              testTier: "groq",
              configured: modelsQuery.data.groq_configured,
            })}

            <p className="mt-5 mb-3 text-xs font-semibold tracking-wide text-slate-400 uppercase">
              CV analysis model
            </p>
            {renderModelRow("gemini_model", "Gemini model", modelsQuery.data.gemini_model, {
              testTier: "gemini",
              configured: modelsQuery.data.gemini_configured,
            })}
          </div>
        )}
        {updateMutation.isError && (
          <div className="mt-2">
            <ErrorBanner message="Failed to save — see server logs" />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>AI usage</SectionTitle>
        <p className="mb-3 text-sm text-slate-600">
          Every LLM call the router made in the last {usageQuery.data?.since_hours ?? 24} hours,
          by capability and outcome. Failures here are normal in small numbers — a rate limit
          means the next leg served the call — but a capability that is mostly failing is a
          problem the badges above won't show.
        </p>
        {usageQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {usageQuery.data && usageQuery.data.rows.length === 0 && (
          <p className="text-sm text-slate-500">
            No calls recorded yet — the ledger is flushed from Redis every few minutes.
          </p>
        )}
        {usageQuery.data && usageQuery.data.rows.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 uppercase">
                <th className="py-1">Capability</th>
                <th className="py-1">Outcome</th>
                <th className="py-1 text-right">Calls</th>
              </tr>
            </thead>
            <tbody>
              {usageQuery.data.rows.map((row) => (
                <tr key={`${row.capability}:${row.outcome}`} className="border-t border-slate-100">
                  <td className="py-1 text-slate-600">{row.capability}</td>
                  <td className={`py-1 ${row.outcome === "ok" ? "text-slate-500" : "text-amber-700"}`}>
                    {row.outcome}
                  </td>
                  <td className="py-1 text-right text-slate-600">{row.calls}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card>
        <SectionTitle>Redis &amp; Celery</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Both hold only transient state — nothing here is a system of record, so clearing either
          is always safe to retry.
        </p>
        <div className="flex flex-wrap gap-3">
          <Button className="bg-slate-600 hover:bg-slate-500" onClick={() => setIsFlushOpen(true)}>
            Clear Redis
          </Button>
          <Button className="bg-slate-600 hover:bg-slate-500" onClick={() => setIsPurgeOpen(true)}>
            Clear Celery queue
          </Button>
        </div>
        {flushMutation.isSuccess && (
          <p className="mt-3 text-sm text-green-700">
            Flushed {flushMutation.data.databases_flushed} Redis database(s).
          </p>
        )}
        {purgeMutation.isSuccess && (
          <p className="mt-3 text-sm text-green-700">
            Purged {purgeMutation.data.purged} queued task(s).
          </p>
        )}
      </Card>

      {isFlushOpen && (
        <Modal title="Clear Redis" onClose={() => setIsFlushOpen(false)}>
          <p className="mb-3 text-sm text-slate-600">
            Flushes every Redis database this app uses — Groq/Gemini circuit-breaker cooldowns,
            the daily "should I apply?" rerank budget, Celery's broker queue and stored task
            results, <strong>and any AI model overrides set above</strong> (they revert to their
            .env defaults). This never touches Postgres — no job, match, or profile data is
            affected.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              className="bg-slate-600 hover:bg-slate-500"
              onClick={() => setIsFlushOpen(false)}
              disabled={flushMutation.isPending}
            >
              Cancel
            </Button>
            <Button onClick={() => flushMutation.mutate()} disabled={flushMutation.isPending}>
              {flushMutation.isPending ? "Flushing…" : "Confirm"}
            </Button>
          </div>
          {flushMutation.isError && (
            <div className="mt-2">
              <ErrorBanner message="Failed to flush Redis" />
            </div>
          )}
        </Modal>
      )}

      {isPurgeOpen && (
        <Modal title="Clear Celery queue" onClose={() => setIsPurgeOpen(false)}>
          <p className="mb-3 text-sm text-slate-600">
            Discards every task still waiting in the queue that no worker has picked up yet — the
            fix for a backlog stuck behind e.g. a bad scrape or a "rescore all vacancies" fan-out
            gone wrong. Does not stop a task a worker has already started.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              className="bg-slate-600 hover:bg-slate-500"
              onClick={() => setIsPurgeOpen(false)}
              disabled={purgeMutation.isPending}
            >
              Cancel
            </Button>
            <Button onClick={() => purgeMutation.mutate()} disabled={purgeMutation.isPending}>
              {purgeMutation.isPending ? "Purging…" : "Confirm"}
            </Button>
          </div>
          {purgeMutation.isError && (
            <div className="mt-2">
              <ErrorBanner message="Failed to purge Celery queue" />
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
