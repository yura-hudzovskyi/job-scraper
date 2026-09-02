import { apiClient } from "./client";
import type {
  ActiveCv,
  ConnectTelegramResponse,
  CvDocument,
  JobDetail,
  JobListResponse,
  MeResponse,
  NotificationSettings,
  PipelineConfig,
  PipelineRun,
  Preferences,
  ProfileSummary,
  ResetResponse,
  ScrapeRun,
  SourceHealth,
  SystemStatus,
  TelegramBotInfo,
  TelegramStatus,
  TestVoyageResponse,
  TokenResponse,
} from "./types";

// --- auth -------------------------------------------------------------------

export const register = (email: string, password: string) =>
  apiClient.post<TokenResponse>("/api/auth/register", { email, password });
export const login = (email: string, password: string) =>
  apiClient.post<TokenResponse>("/api/auth/login", { email, password });
export const getMe = () => apiClient.get<MeResponse>("/api/auth/me");

// --- CV ---------------------------------------------------------------------

export function uploadCv(file: File): Promise<CvDocument> {
  const form = new FormData();
  form.append("file", file);
  return apiClient.postForm<CvDocument>("/api/cv", form);
}

export const listCvs = () => apiClient.get<CvDocument[]>("/api/cv");
export const getActiveCv = () => apiClient.get<ActiveCv>("/api/cv/active");
export const deleteCv = (cvId: string) => apiClient.delete<void>(`/api/cv/${cvId}`);
export const getProfile = () => apiClient.get<ProfileSummary>("/api/profile");

// --- preferences & notifications --------------------------------------------

export const getPreferences = () => apiClient.get<Preferences | null>("/api/settings");
export const updatePreferences = (preferences: Preferences) =>
  apiClient.patch<Preferences>("/api/settings", preferences);

export const getNotificationSettings = () =>
  apiClient.get<NotificationSettings>("/api/settings/notifications");
export const updateNotificationSettings = (settings: NotificationSettings) =>
  apiClient.patch<NotificationSettings>("/api/settings/notifications", settings);

// --- telegram ---------------------------------------------------------------

export const getTelegramStatus = () =>
  apiClient.get<TelegramStatus>("/api/integrations/telegram/status");
export const getTelegramBotInfo = () =>
  apiClient.get<TelegramBotInfo>("/api/integrations/telegram/bot-info");
export const connectTelegram = (chatId: string) =>
  apiClient.post<ConnectTelegramResponse>("/api/integrations/telegram/connect", {
    chat_id: chatId,
  });
export const testTelegram = () =>
  apiClient.post<{ status: string }>("/api/integrations/telegram/test");

// --- sources ----------------------------------------------------------------

export const listSources = () => apiClient.get<SourceHealth[]>("/api/sources");
export const listScrapeRuns = () => apiClient.get<ScrapeRun[]>("/api/sources/runs");

// --- jobs -------------------------------------------------------------------

export const listJobs = (limit: number, offset: number, includeSkipped = false) =>
  apiClient.get<JobListResponse>(
    `/api/jobs?limit=${limit}&offset=${offset}&include_skipped=${includeSkipped}`,
  );
export const getJob = (jobId: string) => apiClient.get<JobDetail>(`/api/jobs/${jobId}`);
/** Re-run search + rerank against the vacancies already in the database. */
export const rematch = () => apiClient.post<{ status: string }>("/api/jobs/rematch");

// --- system -----------------------------------------------------------------

export const getSystemStatus = () => apiClient.get<SystemStatus>("/api/system/status");
export const getPipelineConfig = () => apiClient.get<PipelineConfig>("/api/system/config");
export const updatePipelineConfig = (values: Record<string, string | number | boolean>) =>
  apiClient.patch<PipelineConfig>("/api/system/config", { values });
export const resetPipelineConfig = () =>
  apiClient.post<PipelineConfig>("/api/system/config/reset");
export const testVoyage = () => apiClient.post<TestVoyageResponse>("/api/system/config/test");

export type RunSteps = "full" | "match" | "scrape";
export const runPipeline = (steps: RunSteps) =>
  apiClient.post<{ status: string; task: string }>(`/api/system/run?steps=${steps}`);
export const listPipelineRuns = () => apiClient.get<PipelineRun[]>("/api/system/runs");

export type ResetTarget = "notifications" | "matches" | "embeddings" | "jobs" | "all";
export const resetData = (target: ResetTarget) =>
  apiClient.post<ResetResponse>(`/api/system/reset/${target}`);
export const purgeQueue = () => apiClient.post<ResetResponse>("/api/system/queue/purge");
export const flushRedis = () => apiClient.post<ResetResponse>("/api/system/redis/flush");
