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
  last_seen_at: string | null;
  job_sources: OfferSource[] | null;
};

export const OFFER_SELECT =
  "id,title,company,location,is_remote,score,score_breakdown,posted_at,last_seen_at,job_sources(source,url,url_direct)";

/**
 * The best outbound link for an offer: a canonical employer/ATS URL when a
 * source carried one, otherwise the first portal URL we scraped.
 */
export function applyUrl(offer: Offer): string | null {
  const sources = offer.job_sources ?? [];
  const direct = sources.find((item) => item.url_direct);
  return direct?.url_direct ?? sources[0]?.url ?? null;
}
