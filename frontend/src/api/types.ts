// --- CV ---------------------------------------------------------------------

export interface CvDocument {
  id: string;
  filename: string;
  uploaded_at: string;
  characters: number;
  text_preview: string;
  /** Exactly one CV is active — the newest. It is the one that gets embedded. */
  active: boolean;
}

export interface ActiveCv {
  cv: CvDocument | null;
  /** The exact text handed to the embedding and rerank models. */
  model_document: string;
}

// --- preferences ------------------------------------------------------------

export interface Preferences {
  desired_salary_usd: number | null;
  preferred_roles: string[];
  preferred_stack: string[];
  blocked_stack: string[];
  work_formats: string[];
  locations: string[];
  max_required_experience: number | null;
  companies_blacklist: string[];
}

export const EMPTY_PREFERENCES: Preferences = {
  desired_salary_usd: null,
  preferred_roles: [],
  preferred_stack: [],
  blocked_stack: [],
  work_formats: [],
  locations: [],
  max_required_experience: null,
  companies_blacklist: [],
};

export interface ProfileSummary {
  user_id: string;
  cv_count: number;
  has_preferences: boolean;
}

export interface NotificationSettings {
  enabled: boolean;
  min_score: number;
  quiet_hours_start: number;
  quiet_hours_end: number;
}

// --- jobs & matches ---------------------------------------------------------

/** A match is two numbers and the weight between them. `score` is always
 *  reproducible: similarity when relevance is null, otherwise
 *  similarity*(1-weight) + relevance*weight. */
export interface JobMatch {
  id: string;
  eligible: boolean;
  /** Which of the user's own rules rejected this vacancy, when eligible is false. */
  filter_reasons: string[];
  score: number;
  similarity: number;
  /** Null when the reranker never saw this job — not a zero. */
  relevance: number | null;
  rerank_position: number | null;
  recommendation: string;
  embedding_model: string | null;
  rerank_model: string | null;
  rerank_weight: number | null;
  decision: string;
  scored_at: string | null;
}

export interface JobSummary {
  id: string;
  title: string;
  company: string;
  description: string;
  source_count: number;
  match: JobMatch | null;
}

export interface JobDetail extends JobSummary {
  /** The exact text the models were given for this vacancy. */
  model_document: string;
}

export interface JobListResponse {
  items: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

// --- sources ----------------------------------------------------------------

export interface SourceHealth {
  source_name: string;
  raw_jobs_stored: number;
  categories: string[];
}

export interface ScrapeRun {
  source: string;
  category: string | null;
  started_at: string;
  jobs_seen: number;
  new_count: number;
  errors: number;
}

// --- system -----------------------------------------------------------------

export interface ConfigField {
  name: string;
  value: string | number | boolean;
  default: string | number | boolean;
  type: "str" | "int" | "float" | "bool";
  description: string;
  minimum: number | null;
  maximum: number | null;
}

export interface PipelineConfig {
  fields: ConfigField[];
}

export interface EmbeddingStatus {
  model: string;
  jobs_embedded: number;
  jobs_total: number;
  profiles_embedded: number;
  /** Vectors left over from a previously configured model. */
  stale_vectors: number;
}

export interface PipelineStep {
  name: string;
  status?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface PipelineRun {
  id: string;
  trigger: string;
  status: "running" | "succeeded" | "failed";
  steps: PipelineStep[];
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface SystemStatus {
  ready: boolean;
  /** Why a run wouldn't produce matches. Empty when ready. */
  blockers: string[];
  voyage_configured: boolean;
  telegram_configured: boolean;
  scrape_interval_seconds: number;
  sources: Record<string, number>;
  categories: Record<string, string[]>;
  counts: Record<string, number>;
  embeddings: EmbeddingStatus;
  config: PipelineConfig;
  active_run: PipelineRun | null;
  recent_runs: PipelineRun[];
}

export interface TestVoyageResponse {
  embedding_ok: boolean;
  embedding_dimension: number | null;
  rerank_ok: boolean;
  error: string | null;
}

export interface ResetResponse {
  deleted: Record<string, number>;
}

// --- taxonomy ---------------------------------------------------------------

export interface TaxonomyStatus {
  namespace: string;
  version: string;
  status: string;
  languages: string[];
  concepts: number;
  relations: number;
  /** Checksum of the release archive — two installs on "1.2.1" can still differ. */
  source_checksum: string | null;
  pending_unmapped: number;
}

/** A term the linker found in a document and the taxonomy did not cover. */
export interface UnmappedTerm {
  normalized_text: string;
  sample_raw_text: string;
  occurrences: number;
}

/** `pending` is the undo — it returns a term to the queue. */
export type UnmappedDecision = "ignored" | "promoted" | "pending";

// --- telegram & auth --------------------------------------------------------

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
