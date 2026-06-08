import { createClient } from "@supabase/supabase-js";

// Local Supabase instance (Docker). Used for realtime / auth in later features.
const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://localhost:8000";
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "your-local-anon-key";

export const supabase = createClient(url, anonKey);
