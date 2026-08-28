import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listJobs } from "../api/endpoints";
import { Card, SectionTitle } from "../components/ui";

export function Jobs() {
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs });

  return (
    <Card>
      <SectionTitle>Jobs</SectionTitle>
      {jobsQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {jobsQuery.data?.length === 0 && (
        <p className="text-sm text-slate-500">
          No jobs yet — trigger a sync on the Sources page, or wait for the next scheduled scrape.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {jobsQuery.data?.map((job) => (
          <li key={job.id}>
            <Link
              to={`/jobs/${job.id}`}
              className="block rounded border border-slate-200 p-3 hover:border-slate-400"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{job.title}</span>
                {job.source_count > 1 && (
                  <span className="text-xs text-slate-500">{job.source_count} sources</span>
                )}
              </div>
              <p className="text-sm text-slate-600">{job.company}</p>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
