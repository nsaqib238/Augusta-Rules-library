import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.REACT_APP_SUPABASE_URL || '';
const supabaseAnonKey = process.env.REACT_APP_SUPABASE_ANON_KEY || '';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

export type AuthUser = {
  id: string;
  email: string;
  created_at: string;
};

export type Document = {
  id: string;
  user_id: string;
  filename: string;
  codebook: string;
  processing_model: 'spacy' | 'bert';
  status: 'processing' | 'completed' | 'failed';
  created_at: string;
  updated_at: string;
  file_size: number;
  chunks_count?: number;
  processing_time_seconds?: number;
  model_path?: string;
  analysis_depth?: string;
}; 