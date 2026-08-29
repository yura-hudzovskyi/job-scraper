import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getProfile, getTelegramStatus, listJobs, listSources } from "../api/endpoints";
import { Button, Card, SectionTitle } from "../components/ui";

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
  const telegramQuery = useQuery({ queryKey: ["telegram-status"], queryFn: getTelegramStatus });

  const rawJobsStored = sourcesQuery.data?.reduce((sum, s) => sum + s.raw_jobs_stored, 0) ?? 0;
  const hasCv = (profileQuery.data?.cv_count ?? 0) > 0;
  const hasPreferences = profileQuery.data?.has_preferences ?? false;
  const hasJobs = rawJobsStored > 0;
  const hasTelegram = telegramQuery.data?.connected ?? false;
  const setupComplete = hasCv && hasPreferences && hasJobs && hasTelegram;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Setup</SectionTitle>
        <StatusRow done={hasCv} label="CV uploaded" to="/profile" linkLabel="Upload / analyze" />
        <StatusRow
          done={hasPreferences}
          label="Preferences set"
          to="/settings"
          linkLabel="Configure"
        />
        <StatusRow done={hasJobs} label="Jobs scraped" to="/sources" linkLabel="Sync now" />
        <StatusRow
          done={hasTelegram}
          label="Telegram connected"
          to="/settings"
          linkLabel="Connect + test"
        />
        {setupComplete && (
          <div className="mt-4 flex items-center justify-between rounded border border-green-200 bg-green-50 p-3">
            <p className="text-sm text-green-800">
              Everything's set up — score jobs against your profile to start getting matches.
            </p>
            <Link to="/jobs">
              <Button className="shrink-0">Score jobs →</Button>
            </Link>
          </div>
        )}
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
