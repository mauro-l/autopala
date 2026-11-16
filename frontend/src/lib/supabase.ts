import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.PUBLIC_SUPABASE_URL;
const key = import.meta.env.PUBLIC_SUPABASE_ANON_KEY;

/**
 * False when the build had no `.env`: the UI shows a configuration message
 * instead of rendering an empty list that looks like "no offers".
 */
export const isConfigured = Boolean(url && key);

/**
 * Publishable client only. The service_role key would bypass row-level
 * security and must never reach a browser bundle.
 */
export const supabase =
  url && key
    ? createClient(url, key, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
        },
      })
    : null;

export type OfferSource = {
  source: string;
  url: string;
  url_direct: string | null;
};

export type ScoreParts = {
  role: number;
  stack: number;
  seniority: number;
  experience: number;
  location: number;
  recency: number;
};

/**
 * Written by the worker as `score_breakdown`. Older rows may predate a field,
 * so every member is optional except the total.
 */
export type ScoreBreakdown = {
  parts?: Partial<ScoreParts> | null;
  total?: number | null;
  matched_skills?: string[] | null;
  required_years?: number | null;
  seniority_level?: string | null;
  role_focus?: string | null;
  english_level?: string | null;
  english_required?: boolean | null;
  unpaid?: boolean | null;
  degree_required?: boolean | null;
};

export type Offer = {
  id: string;
  title: string;
  company: string;
  location: string;
  is_remote: boolean;
  score: number | null;
  score_breakdown: ScoreBreakdown | null;
  posted_at: string | null;
  /** When the offer first entered this app. Drives the day grouping. */
  first_seen_at: string | null;
  last_seen_at: string | null;
  job_sources: OfferSource[] | null;
};

/**
 * `first_seen_at` is selected because the day sections group by it when the
 * source gave no `posted_at`. `last_seen_at` stays for the tiebreaker.
 */
export const OFFER_SELECT =
  "id,title,company,location,is_remote,score,score_breakdown,posted_at,first_seen_at,last_seen_at,job_sources(source,url,url_direct)";

/**
 * Owner triage rows. `job_state` is scoped by RLS to `user_id = auth.uid()`, so
 * the owner's rows are what comes back; the filter is passed anyway so the
 * request is explicit and cheap.
 */
export const JOB_STATE_SELECT = "job_id,seen_at,saved_at";

/**
 * The best outbound link for an offer: a canonical employer/ATS URL when a
 * source carried one, otherwise the first portal URL we scraped.
 */
export function applyUrl(offer: Offer): string | null {
  const sources = offer.job_sources ?? [];
  const direct = sources.find((item) => item.url_direct);
  return direct?.url_direct ?? sources[0]?.url ?? null;
}
