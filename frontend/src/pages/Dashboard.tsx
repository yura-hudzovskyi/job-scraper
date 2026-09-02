import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getProfile, getSystemStatus, getTelegramStatus } from "../api/endpoints";
import { Button, Card, InfoBanner, SectionTitle, Stat } from "../components/ui";

function StatusRow({
  done,
  label,
  detail,
  to,
  linkLabel,
}: {
  done: boolean;
  label: string;
  detail: string;
  to: string;
  linkLabel: string;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 py-2 last:border-0">
      <div className="flex items-start gap-2">
        <span className={done ? "text-green-600" : "text-slate-300"}>{done ? "✓" : "○"}</span>
        <div>
          <p className={done ? "text-slate-700" : "text-slate-500"}>{label}</p>
          <p className="text-xs text-slate-500">{detail}</p>
        </div>
      </div>
      <Link to={to} className="shrink-0 text-sm text-slate-600 underline hover:text-slate-900">
        {linkLabel}
      </Link>
    </div>
  );
}

export function Dashboard() {
  const profileQuery = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const statusQuery = useQuery({ queryKey: ["system-status"], queryFn: getSystemStatus });
  const telegramQuery = useQuery({ queryKey: ["telegram-status"], queryFn: getTelegramStatus });

  const status = statusQuery.data;
  const hasCv = (profileQuery.data?.cv_count ?? 0) > 0;
  const hasPreferences = profileQuery.data?.has_preferences ?? false;
  const jobs = status?.counts.canonical_jobs ?? 0;
  const matches = status?.counts.matches ?? 0;
  const hasKey = status?.voyage_configured ?? false;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <SectionTitle>Setup</SectionTitle>
        <StatusRow
          done={hasKey}
          label="Voyage API key"
          detail="Set VOYAGE_API_KEY in .env — without it there is no embedding search and no reranking."
          to="/system"
          linkLabel="Check"
        />
        <StatusRow
          done={hasCv}
          label="CV uploaded"
          detail="Its text is embedded as-is and handed to the reranker."
          to="/profile"
          linkLabel="Upload"
        />
        <StatusRow
          done={hasPreferences}
          label="Preferences set"
          detail="What you want, plus the rules that filter vacancies out."
          to="/settings"
          linkLabel="Configure"
        />
        <StatusRow
          done={jobs > 0}
          label="Vacancies scraped"
          detail="Run the pipeline once to fetch, embed and match."
          to="/system"
          linkLabel="Run pipeline"
        />
        <StatusRow
          done={telegramQuery.data?.connected ?? false}
          label="Telegram connected"
          detail="Optional — matches above your threshold arrive as swipe cards."
          to="/settings"
          linkLabel="Connect"
        />

        {status && status.blockers.length === 0 && matches === 0 && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded border border-green-200 bg-green-50 p-3">
            <p className="text-sm text-green-800">
              Everything's in place — run the pipeline to get your first matches.
            </p>
            <Link to="/system">
              <Button className="shrink-0">Run pipeline →</Button>
            </Link>
          </div>
        )}
        {status && status.blockers.length > 0 && (
          <div className="mt-4">
            <InfoBanner tone="warn">
              <ul className="list-inside list-disc">
                {status.blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </InfoBanner>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>At a glance</SectionTitle>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat value={jobs} label="vacancies" />
          <Stat value={status?.embeddings.jobs_embedded ?? "—"} label="embedded" />
          <Stat value={matches} label="matches" />
          <Stat value={profileQuery.data?.cv_count ?? "—"} label="CVs on file" />
        </div>
        <div className="mt-4 flex gap-4 text-sm">
          <Link to="/jobs" className="text-slate-600 underline hover:text-slate-900">
            Browse jobs →
          </Link>
          <Link to="/system" className="text-slate-600 underline hover:text-slate-900">
            Pipeline &amp; settings →
          </Link>
        </div>
      </Card>
    </div>
  );
}
