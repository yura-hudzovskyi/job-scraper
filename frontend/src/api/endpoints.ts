import { apiClient } from "./client";
import type {
  AiModelsResponse,
  AiModelsUpdateRequest,
  CandidateProfile,
  ConnectTelegramResponse,
  CvDocument,
  FlushRedisResponse,
  JobListResponse,
  JobMatch,
  JobSummary,
  MeResponse,
  NotificationThresholds,
  Preferences,
  ProfileSummary,
  PurgeCeleryResponse,
  SourceHealth,
  SuggestedPreferences,
  TelegramBotInfo,
  TelegramStatus,
  TestModelResponse,
  TokenResponse,
} from "./types";

export const register = (email: string, password: string) =>
  apiClient.post<TokenResponse>("/api/auth/register", { email, password });
export const login = (email: string, password: string) =>
  apiClient.post<TokenResponse>("/api/auth/login", { email, password });
export const getMe = () => apiClient.get<MeResponse>("/api/auth/me");

export function uploadCv(file: File): Promise<CvDocument> {
  const form = new FormData();
  form.append("file", file);
  return apiClient.postForm<CvDocument>("/api/cv", form);
}

export const listCvs = () => apiClient.get<CvDocument[]>("/api/cv");
export const deleteCv = (cvId: string) => apiClient.delete<void>(`/api/cv/${cvId}`);
export const analyzeCv = () => apiClient.post<CandidateProfile>("/api/cv/analyze");
export const getCandidateProfile = () => apiClient.get<CandidateProfile | null>("/api/cv/profile");
export const correctSkill = (name: string) =>
  apiClient.post<CandidateProfile>("/api/cv/profile/skills", { name });
export const removeSkill = (name: string) =>
  apiClient.delete<CandidateProfile>(`/api/cv/profile/skills/${encodeURIComponent(name)}`);
export const getProfile = () => apiClient.get<ProfileSummary>("/api/profile");

export const getPreferences = () => apiClient.get<Preferences | null>("/api/settings");
export const updatePreferences = (preferences: Preferences) =>
  apiClient.patch<Preferences>("/api/settings", preferences);
export const aiFillPreferences = () =>
  apiClient.post<SuggestedPreferences>("/api/settings/preferences/ai-fill");

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

export const listSources = () => apiClient.get<SourceHealth[]>("/api/sources");
export const syncSource = (sourceName: string) =>
  apiClient.post<{ status: string; source: string }>(`/api/sources/${sourceName}/sync`);

export const listJobs = (limit: number, offset: number, includeSkipped = false) =>
  apiClient.get<JobListResponse>(
    `/api/jobs?limit=${limit}&offset=${offset}&include_skipped=${includeSkipped}`,
  );
export const getJob = (jobId: string) => apiClient.get<JobSummary>(`/api/jobs/${jobId}`);
export const getJobMatch = (jobId: string) => apiClient.get<JobMatch>(`/api/jobs/${jobId}/match`);
export const rescoreJob = (jobId: string) =>
  apiClient.post<{ status: string; job_id: string }>(`/api/jobs/${jobId}/rescore`);
/** Ask for an LLM review of this one match now, ahead of the daily ranking. */
export const analyzeJob = (jobId: string) =>
  apiClient.post<{ status: string; job_id: string }>(`/api/jobs/${jobId}/analyze`);
export const rescoreAllJobs = () =>
  apiClient.post<{ status: string }>("/api/jobs/rescore-all");

export const getNotificationThresholds = () =>
  apiClient.get<NotificationThresholds>("/api/settings/notifications");
export const updateNotificationThresholds = (thresholds: NotificationThresholds) =>
  apiClient.patch<NotificationThresholds>("/api/settings/notifications", thresholds);

export const getAiModels = () => apiClient.get<AiModelsResponse>("/api/ai/models");
export const updateAiModels = (payload: AiModelsUpdateRequest) =>
  apiClient.patch<AiModelsResponse>("/api/ai/models", payload);
export const testAiModel = (tier: "groq" | "gemini", model: string) =>
  apiClient.post<TestModelResponse>("/api/ai/models/test", { tier, model });

export const flushRedis = () => apiClient.post<FlushRedisResponse>("/api/system/redis/flush");
export const purgeCelery = () => apiClient.post<PurgeCeleryResponse>("/api/system/celery/purge");
