import { Route, Routes } from "react-router-dom";

import { Applications } from "./pages/Applications";
import { Dashboard } from "./pages/Dashboard";
import { JobDetails } from "./pages/JobDetails";
import { Jobs } from "./pages/Jobs";
import { MarketInsights } from "./pages/MarketInsights";
import { Profile } from "./pages/Profile";
import { Settings } from "./pages/Settings";
import { Sources } from "./pages/Sources";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/jobs" element={<Jobs />} />
      <Route path="/jobs/:jobId" element={<JobDetails />} />
      <Route path="/applications" element={<Applications />} />
      <Route path="/profile" element={<Profile />} />
      <Route path="/market-insights" element={<MarketInsights />} />
      <Route path="/sources" element={<Sources />} />
      <Route path="/settings" element={<Settings />} />
    </Routes>
  );
}
