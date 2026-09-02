import { useQuery } from "@tanstack/react-query";

import { listScrapeRuns, listSources } from "../api/endpoints";
import { Badge, Card, SectionTitle } from "../components/ui";

export function Sources() {
  const sourcesQuery = useQuery({ queryKey: ["sources"], queryFn: listSources });
  const runsQuery = useQuery({ queryKey: ["scrape-runs"], queryFn: listScrapeRuns });

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Sources</SectionTitle>
        <p className="mb-4 text-sm text-slate-600">
          Each pipeline run scrapes one category per source — whichever has gone longest without
          one — so the rotation reaches every category over time instead of re-reading the same
          feed. Scraping is triggered from the System page, together with embedding and matching.
        </p>
        {sourcesQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        <ul className="flex flex-col gap-2">
          {sourcesQuery.data?.map((source) => (
            <li key={source.source_name} className="rounded border border-slate-200 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium capitalize">{source.source_name}</p>
                <span className="text-sm text-slate-500">
                  {source.raw_jobs_stored} raw jobs stored
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {source.categories.map((category) => (
                  <Badge key={category}>{category}</Badge>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <SectionTitle>Recent scrapes</SectionTitle>
        <p className="mb-3 text-sm text-slate-600">
          "Seen" is how many listings the category page offered; "new" is how many were not already
          known and were fetched in full.
        </p>
        {runsQuery.data?.length === 0 && (
          <p className="text-sm text-slate-500">Nothing scraped yet.</p>
        )}
        {runsQuery.data && runsQuery.data.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 uppercase">
                <th className="py-1">When</th>
                <th className="py-1">Source</th>
                <th className="py-1">Category</th>
                <th className="py-1 text-right">Seen</th>
                <th className="py-1 text-right">New</th>
              </tr>
            </thead>
            <tbody>
              {runsQuery.data.map((run) => (
                <tr key={`${run.source}-${run.started_at}`} className="border-t border-slate-100">
                  <td className="py-1 text-slate-500">
                    {new Date(run.started_at).toLocaleString()}
                  </td>
                  <td className="py-1 text-slate-600 capitalize">{run.source}</td>
                  <td className="py-1 text-slate-600">{run.category ?? "—"}</td>
                  <td className="py-1 text-right tabular-nums text-slate-600">{run.jobs_seen}</td>
                  <td className="py-1 text-right tabular-nums text-slate-600">
                    {run.errors > 0 ? <Badge tone="bad">failed</Badge> : run.new_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
