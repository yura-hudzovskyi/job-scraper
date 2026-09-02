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
  /** "llm" | "rules" | "user" — a skill the user corrected is marked as theirs. */
  source: string;
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

export interface SuggestedPreferences extends Preferences {
  model_label: string;
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

export interface MatchReason {
  label: string;
  detail: string;
}

export interface MatchGap {
  label: string;
  critical: boolean;
}

export interface DocumentVersion {
  version: number;
  content_hash: string;
}

export interface PipelineVersions {
  scorer: string;
  match_prompt: string;
  skill_taxonomy: string;
  rerank_instruction: string | null;
  calibration: string | null;
}

/** How a match was produced — stored with the result, so it keeps naming the
 *  models that really ran even after the System page changes them. */
export interface MatchProvenance {
  engine: string;
  analysis_level: string;
  profile: DocumentVersion | null;
  job: DocumentVersion | null;
  embedding_model: string | null;
  cross_encoder_model: string | null;
  skills_model: string | null;
  rerank_model: string | null;
  match_model: string | null;
  fallback_reason: string | null;
  versions: PipelineVersions;
  generated_at: string | null;
}

export interface JobMatch {
  id: string;
  eligible: boolean;
  requirement_match: number;
  practical_fit: number;
  breakdown: ScoreBreakdown;
  strengths: MatchReason[];
  gaps: MatchGap[];
  recommendation: string | null;
  /** How much evidence stood behind the score, 0-1. Null for matches scored
   *  before the hybrid engine — shown as "not recorded", never as zero. */
  confidence: number | null;
  /** What the result could not establish. Never gaps. */
  risks: string[];
  llm_assessment: LlmAssessment | null;
  provenance: MatchProvenance | null;
  scored_at: string | null;
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

export interface NotificationThresholds {
  immediate_threshold: number;
  conditional_threshold: number;
  digest_threshold: number;
  strong_component_threshold: number;
  quiet_hours_start: number;
  quiet_hours_end: number;
}

export interface AiModelField {
  value: string;
  is_override: boolean;
  default: string;
}

/** One provider/model pair as the router currently sees it. */
export interface LegStatus {
  provider: string;
  model: string;
  available: boolean;
  /** Why it isn't: rate_limit, quota_exhausted, transient, fatal. */
  reason: string | null;
  retry_after_seconds: number | null;
}

export interface CapabilityStatus {
  capability: string;
  legs: LegStatus[];
  budget_used: number;
  budget_limit: number;
}

/** One embedding lane: its own vector space, and how much of the corpus it has
 *  indexed. A lane only answers queries once it covers nearly everything. */
export interface LaneStatus {
  id: string;
  provider: string;
  model: string;
  dimension: number;
  role: string;
  state: string;
  jobs_covered: number;
  jobs_total: number;
}

export interface AiModelsResponse {
  groq_configured: boolean;
  groq_model: AiModelField;
  gemini_configured: boolean;
  gemini_model: AiModelField;
  capabilities: CapabilityStatus[];
  lanes: LaneStatus[];
}

export interface AiModelsUpdateRequest {
  groq_model?: string | null;
  gemini_model?: string | null;
}

export interface TestModelResponse {
  ok: boolean;
  model_label: string | null;
  error: string | null;
}

export interface FlushRedisResponse {
  databases_flushed: number;
}

export interface PurgeCeleryResponse {
  purged: number;
}
