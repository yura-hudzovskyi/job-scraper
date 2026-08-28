import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getProfile, listJobs, listSources } from "../api/endpoints";
import { Card, SectionTitle } from "../components/ui";

function StatusRow({
  done,
  label,
  to,
  linkLabel,
}: {
  done: boolean;
  label: string;
  to: string;
  linkLabel: string;
}) {
  return (
    <div className="flex items-center justify-between border-b border-slate-100 py-2 last:border-0">
      <div className="flex items-center gap-2">
        <span className={done ? "text-green-600" : "text-slate-300"}>{done ? "✓" : "○"}</span>
        <span className={done ? "text-slate-700" : "text-slate-500"}>{label}</span>
      </div>
      <Link to={to} className="text-sm text-slate-600 underline hover:text-slate-900">
        {linkLabel}
      </Link>
    </div>
  );
}

export function Dashboard() {
  const profileQuery = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const sourcesQuery = useQuery({ queryKey: ["sources"], queryFn: listSources });
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs });

  const rawJobsStored = sourcesQuery.data?.reduce((sum, s) => sum + s.raw_jobs_stored, 0) ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Setup</SectionTitle>
        <StatusRow
          done={(profileQuery.data?.cv_count ?? 0) > 0}
          label="CV uploaded"
          to="/profile"
          linkLabel="Upload / analyze"
        />
        <StatusRow
          done={profileQuery.data?.has_preferences ?? false}
          label="Preferences set"
          to="/settings"
          linkLabel="Configure"
        />
        <StatusRow done={rawJobsStored > 0} label="Jobs scraped" to="/sources" linkLabel="Sync now" />
        <StatusRow
          done={false}
          label="Telegram connected"
          to="/settings"
          linkLabel="Connect + test"
        />
      </Card>

      <Card>
        <SectionTitle>At a glance</SectionTitle>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold">{jobsQuery.data?.length ?? "—"}</p>
            <p className="text-sm text-slate-500">canonical jobs</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{rawJobsStored}</p>
            <p className="text-sm text-slate-500">raw jobs stored</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{profileQuery.data?.cv_count ?? "—"}</p>
            <p className="text-sm text-slate-500">CVs on file</p>
          </div>
        </div>
        <div className="mt-4 text-center">
          <Link to="/jobs" className="text-sm text-slate-600 underline hover:text-slate-900">
            Browse jobs →
          </Link>
        </div>
      </Card>
    </div>
  );
}
