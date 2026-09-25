export type User = { id: number; username: string };

export type Keyword = {
  id: number;
  phrase: string;
  is_active: boolean;
  last_polled_at: string | null;
  created_at: string;
};

export type StopWord = { id: number; word_or_phrase: string; created_at: string };

export type Post = {
  id: number;
  threads_post_id: string;
  text: string;
  author_username: string;
  permalink: string;
  post_timestamp: string | null;
  likes_count: number;
  replies_count: number;
  matched_keyword_id: number | null;
  matched_keyword: string | null;
  is_relevant: boolean;
  is_duplicate: boolean;
  duplicate_of_id: number | null;
  is_read: boolean;
  created_at: string;
};

export type PostPage = {
  items: Post[];
  total: number;
  page: number;
  page_size: number;
};

export type Settings = {
  threads_access_token_configured: boolean;
  threads_user_id: string;
  threads_verify_token: string;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_pass_configured: boolean;
  smtp_from: string;
  smtp_use_tls: boolean;
  whatsapp_recipients: string[];
  email_recipients: string[];
};

export type CollectResult = {
  keyword_id: number | null;
  scanned: number;
  saved: number;
  error: string | null;
};

export type WhatsAppStatus = { connected: boolean; logged_in: boolean; phone: string };
