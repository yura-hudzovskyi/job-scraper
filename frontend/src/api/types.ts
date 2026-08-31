export interface CvDocument {
  id: string;
  filename: string;
  uploaded_at: string;
  text_preview: string;
}

export interface CandidateSkill {
  name: string;
  level: string;
  years: number | null;
}

export interface ExperienceEntry {
  company: string;
  title: string;
  start_date: string;
  end_date: string | null;
  description: string;
  skills: string[];
}

export interface CandidateProfile {
  id: string;
  experience_years: number;
  roles: string[];
  skills: CandidateSkill[];
  experience: ExperienceEntry[];
  achievements: string[];
  domains: string[];
  ai_experience: string[];
  generated_by: string | null;
}

export interface ProfileSummary {
  user_id: string;
  cv_count: number;
  has_preferences: boolean;
}

export interface Preferences {
  desired_salary_usd: number | null;
  preferred_roles: string[];
  preferred_stack: string[];
  acceptable_stack: string[];
  blocked_stack: string[];
  work_formats: string[];
  locations: string[];
  max_required_experience: number | null;
  industries_blacklist: string[];
  companies_blacklist: string[];
}

export const EMPTY_PREFERENCES: Preferences = {
  desired_salary_usd: null,
  preferred_roles: [],
  preferred_stack: [],
  acceptable_stack: [],
  blocked_stack: [],
  work_formats: [],
  locations: [],
  max_required_experience: null,
  industries_blacklist: [],
  companies_blacklist: [],
};

export interface SourceHealth {
  source_name: string;
  raw_jobs_stored: number;
}

export interface JobSummary {
  id: string;
  title: string;
  company: string;
  description: string;
  source_count: number;
  practical_fit: number | null;
  recommendation: string | null;
}

export interface JobListResponse {
  items: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ScoreBreakdown {
  skills: number;
  role: number;
  experience: number;
  semantic_fit: number;
  salary: number;
  location: number;
  transferable_skills: number;
  preferences: number;
}

export interface LlmAssessment {
  overall_fit: number;
  recommendation: string;
  confidence: number;
  strengths: string[];
  gaps: string[];
  critical_gaps: string[];
  transferable_experience: string[];
  interview_risk: string;
  summary: string;
  recommended_cv: string | null;
  model_label: string;
}

export interface JobMatch {
  id: string;
  eligible: boolean;
  requirement_match: number;
  practical_fit: number;
  breakdown: ScoreBreakdown;
  strengths: string[];
  gaps: string[];
  recommendation: string | null;
  llm_assessment: LlmAssessment | null;
  skills_source: string | null;
}

export interface ConnectTelegramResponse {
  status: string;
  bot_username: string | null;
}

export interface TelegramStatus {
  connected: boolean;
}

export interface TelegramBotInfo {
  username: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  email: string;
}

export interface MeResponse {
  user_id: string;
  email: string;
}

export interface OllamaModelsResponse {
  models: string[];
}

export interface NotificationThresholds {
  immediate_threshold: number;
  conditional_threshold: number;
  digest_threshold: number;
  strong_component_threshold: number;
  quiet_hours_start: number;
  quiet_hours_end: number;
}
