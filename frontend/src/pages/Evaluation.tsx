import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";

import {
  getEvaluationProgress,
  getEvaluationReport,
  judgeEvaluationPair,
  nextEvaluationPair,
  sampleEvaluationPairs,
  unjudgeEvaluationPair,
} from "../api/endpoints";
import type { EvaluationLabel } from "../api/types";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  InfoBanner,
  SecondaryButton,
  SectionTitle,
  Stat,
} from "../components/ui";

/** Spec 20.1's scale, with the keyboard shortcut that makes a minute a minute. */
const LABELS: { value: EvaluationLabel; key: string; title: string; blurb: string }[] = [
  { value: 0, key: "1", title: "Irrelevant", blurb: "I would not open this" },
  { value: 1, key: "2", title: "Weak", blurb: "Adjacent, but not what I want" },
  { value: 2, key: "3", title: "Relevant", blurb: "Worth reading properly" },
  { value: 3, key: "4", title: "Strong", blurb: "I would apply to this" },
];

function metric(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : value.toFixed(3);
}

export function Evaluation() {
  const queryClient = useQueryClient();

  const pairQuery = useQuery({ queryKey: ["evaluation-next"], queryFn: nextEvaluationPair });
  const progressQuery = useQuery({
    queryKey: ["evaluation-progress"],
    queryFn: getEvaluationProgress,
  });
  const reportQuery = useQuery({ queryKey: ["evaluation-report"], queryFn: getEvaluationReport });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["evaluation-next"] });
    queryClient.invalidateQueries({ queryKey: ["evaluation-progress"] });
  };

  const judgeMutation = useMutation({
    mutationFn: ({ pairId, label }: { pairId: string; label: EvaluationLabel }) =>
      judgeEvaluationPair(pairId, label),
    onSuccess: refresh,
  });
  const undoMutation = useMutation({
    mutationFn: (pairId: string) => unjudgeEvaluationPair(pairId),
    onSuccess: refresh,
  });
  const sampleMutation = useMutation({
    mutationFn: () => sampleEvaluationPairs(300),
    onSuccess: refresh,
  });

  const pair = pairQuery.data;
  const progress = progressQuery.data;
  const busy = judgeMutation.isPending || undoMutation.isPending;

  const judge = useCallback(
    (label: EvaluationLabel) => {
      if (pair && !busy) judgeMutation.mutate({ pairId: pair.id, label });
    },
    [pair, busy, judgeMutation],
  );

  // Number keys, because the budget is a minute per pair and reaching for the
  // mouse between every one of three hundred judgements is most of that minute.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const match = LABELS.find((option) => option.key === event.key);
      if (match) {
        event.preventDefault();
        judge(match.value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [judge]);

  const judged = progress?.counts.seed_judged ?? 0;
  const total = progress?.counts.seed_total ?? 0;
  const lastJudgedId = judgeMutation.data ? judgeMutation.variables?.pairId : undefined;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Evaluation set</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Judging vacancies against your CV builds the yardstick everything else is measured with.
          Until it exists, extraction is not allowed to affect any score — that is the gate, not a
          formality. Roughly a minute each; the seed tier is 300.
        </p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat value={judged} label="judged" />
          <Stat value={total} label="in the set" />
          <Stat value={reportQuery.data?.metrics.relevant ?? 0} label="relevant found" />
          <Stat value={total - judged} label="left to judge" />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <SecondaryButton
            onClick={() => sampleMutation.mutate()}
            disabled={sampleMutation.isPending}
          >
            {sampleMutation.isPending ? "Sampling…" : "Add 300 pairs to judge"}
          </SecondaryButton>
          {progress &&
            Object.entries(progress.label_distribution).map(([label, count]) => (
              <Badge key={label}>
                {LABELS.find((option) => String(option.value) === label)?.title ?? label}: {count}
              </Badge>
            ))}
        </div>
        {sampleMutation.isSuccess && (
          <div className="mt-3">
            <InfoBanner tone="ok">
              Added {sampleMutation.data.added} pair(s) from {sampleMutation.data.considered}{" "}
              scored vacancies. Languages:{" "}
              {Object.entries(sampleMutation.data.coverage.languages)
                .map(([language, count]) => `${language} ${count}`)
                .join(", ")}
              .
            </InfoBanner>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Judge this vacancy</SectionTitle>
        {pairQuery.isLoading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : !pair ? (
          <InfoBanner>
            Nothing left to judge. Add pairs above, or come back after the next scrape.
          </InfoBanner>
        ) : (
          <>
            <div className="mb-3">
              <h3 className="text-base font-medium text-slate-900">{pair.job_title}</h3>
              <p className="text-sm text-slate-600">{pair.job_company}</p>
            </div>
            <div className="max-h-96 overflow-y-auto rounded border border-slate-200 bg-slate-50 p-3">
              <pre className="text-xs whitespace-pre-wrap text-slate-700">{pair.job_text}</pre>
            </div>
            <p className="mt-3 mb-2 text-xs text-slate-600">
              Would you want to see this vacancy, given your CV? Press 1–4 or click.
            </p>
            <div className="flex flex-wrap gap-2">
              {LABELS.map((option) => (
                <Button key={option.value} onClick={() => judge(option.value)} disabled={busy}>
                  <span className="tabular-nums">{option.key}</span> · {option.title}
                  <span className="ml-1 font-normal opacity-70">— {option.blurb}</span>
                </Button>
              ))}
            </div>
            {lastJudgedId && (
              <div className="mt-3 flex items-center gap-3">
                <span className="text-xs text-slate-500">Judged the previous one.</span>
                <SecondaryButton onClick={() => undoMutation.mutate(lastJudgedId)} disabled={busy}>
                  Undo it
                </SecondaryButton>
              </div>
            )}
          </>
        )}
        {(judgeMutation.isError || undoMutation.isError || sampleMutation.isError) && (
          <div className="mt-3">
            <ErrorBanner message="That did not save — see the server logs" />
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>What the numbers say so far</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Spec 20.4. A dash means the set cannot answer that question yet — not a zero. Recall@100
          is the retrieval gate: a reranker cannot recover a vacancy retrieval never returned.
        </p>
        {reportQuery.isLoading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : !reportQuery.data ? (
          <InfoBanner tone="warn">No report yet.</InfoBanner>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat value={metric(reportQuery.data.metrics.ndcg_at["10"])} label="nDCG@10" />
              <Stat value={metric(reportQuery.data.metrics.recall_at["100"])} label="Recall@100" />
              <Stat value={metric(reportQuery.data.metrics.precision_at["10"])} label="P@10" />
              <Stat value={metric(reportQuery.data.metrics.mrr_at_10)} label="MRR@10" />
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Computed over {reportQuery.data.metrics.judged} judged pair(s), of which{" "}
              {reportQuery.data.metrics.relevant} are relevant. {reportQuery.data.metrics.unjudged}{" "}
              ranked vacancies are unjudged and are excluded rather than counted as irrelevant.
            </p>
          </>
        )}
      </Card>
    </div>
  );
}
