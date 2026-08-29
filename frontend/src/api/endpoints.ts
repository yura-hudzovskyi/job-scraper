import { apiClient } from "./client";
import type {
  CandidateProfile,
  ConnectTelegramResponse,
  CvDocument,
  JobMatch,
  JobSummary,
  Preferences,
  ProfileSummary,
  SourceHealth,
} from "./types";

export function uploadCv(file: File): Promise<CvDocument> {
  const form = new FormData();
  form.append("file", file);
  return apiClient.postForm<CvDocument>("/api/cv", form);
}

export const listCvs = () => apiClient.get<CvDocument[]>("/api/cv");
export const analyzeCv = () => apiClient.post<CandidateProfile>("/api/cv/analyze");
export const getCandidateProfile = () => apiClient.get<CandidateProfile | null>("/api/cv/profile");
export const getProfile = () => apiClient.get<ProfileSummary>("/api/profile");

export const getPreferences = () => apiClient.get<Preferences | null>("/api/settings");
export const updatePreferences = (preferences: Preferences) =>
  apiClient.patch<Preferences>("/api/settings", preferences);

export const connectTelegram = (botToken: string, chatId: string) =>
  apiClient.post<ConnectTelegramResponse>("/api/integrations/telegram/connect", {
    bot_token: botToken,
    chat_id: chatId,
  });
export const testTelegram = () =>
  apiClient.post<{ status: string }>("/api/integrations/telegram/test");

export const listSources = () => apiClient.get<SourceHealth[]>("/api/sources");
export const syncSource = (sourceName: string) =>
  apiClient.post<{ status: string; source: string }>(`/api/sources/${sourceName}/sync`);

export const listJobs = () => apiClient.get<JobSummary[]>("/api/jobs");
export const getJob = (jobId: string) => apiClient.get<JobSummary>(`/api/jobs/${jobId}`);
export const getJobMatch = (jobId: string) => apiClient.get<JobMatch>(`/api/jobs/${jobId}/match`);
export const rescoreJob = (jobId: string) =>
  apiClient.post<{ status: string; job_id: string }>(`/api/jobs/${jobId}/rescore`);
