import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  flushRedis,
  getAiModels,
  purgeCelery,
  testAiModel,
  updateAiModels,
} from "../api/endpoints";
import type { AiModelField, AiModelsUpdateRequest } from "../api/types";
import { Button, Card, ErrorBanner, Modal, SectionTitle, inputClass } from "../components/ui";

type ModelFieldKey = keyof AiModelsUpdateRequest;

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
          Changes here take effect on the very next AI call — no redeploy or restart needed. See
          docs/matching-engine.md for how the job pipeline (skill extraction, "should I apply?"
          reranker) and the CV-analysis/preferences pipeline pick a provider.
        </p>
        {modelsQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {modelsQuery.isError && <ErrorBanner message="Failed to load AI model config" />}
        {modelsQuery.data && (
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <Badge
                open={modelsQuery.data.groq_circuit_open}
                label={modelsQuery.data.groq_circuit_open ? "Groq cooling down" : "Groq available"}
              />
              <Badge
                open={modelsQuery.data.gemini_circuit_open}
                label={
                  modelsQuery.data.gemini_circuit_open ? "Gemini cooling down" : "Gemini available"
                }
              />
            </div>
            <p className="mb-3 text-xs font-semibold tracking-wide text-slate-400 uppercase">
              Job pipeline (skill extraction, AI matching) — Groq first, Gemini on rate limit
            </p>
            {renderModelRow("groq_model", "Groq model", modelsQuery.data.groq_model, {
              testTier: "groq",
              configured: modelsQuery.data.groq_configured,
            })}

            <p className="mt-5 mb-3 text-xs font-semibold tracking-wide text-slate-400 uppercase">
              CV analysis / preferences AI-fill — Gemini first, Groq on rate limit
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
