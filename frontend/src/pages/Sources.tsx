import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listSources, syncSource } from "../api/endpoints";
import { Button, Card, SectionTitle } from "../components/ui";

export function Sources() {
  const queryClient = useQueryClient();
  const sourcesQuery = useQuery({ queryKey: ["sources"], queryFn: listSources });

  const syncMutation = useMutation({
    mutationFn: syncSource,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });

  return (
    <Card>
      <SectionTitle>Sources</SectionTitle>
      <p className="mb-4 text-sm text-slate-600">
        Scrape automatically every 2 hours, or trigger a sync now. Raw job count is a rough
        health signal — it should keep growing over time.
      </p>
      {sourcesQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      <ul className="flex flex-col gap-2">
        {sourcesQuery.data?.map((source) => (
          <li
            key={source.source_name}
            className="flex items-center justify-between rounded border border-slate-200 p-3"
          >
            <div>
              <p className="font-medium capitalize">{source.source_name}</p>
              <p className="text-sm text-slate-500">{source.raw_jobs_stored} raw jobs stored</p>
            </div>
            <Button
              onClick={() => syncMutation.mutate(source.source_name)}
              disabled={syncMutation.isPending && syncMutation.variables === source.source_name}
            >
              {syncMutation.isPending && syncMutation.variables === source.source_name
                ? "Queuing…"
                : "Sync now"}
            </Button>
          </li>
        ))}
      </ul>
      {syncMutation.isSuccess && (
        <p className="mt-3 text-sm text-green-700">
          Queued — check back in a bit, this runs in the background.
        </p>
      )}
    </Card>
  );
}
