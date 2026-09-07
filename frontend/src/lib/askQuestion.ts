/**
 * Ask helper (Stage S3b): POST /query/ask, and when the backend queues the
 * question (202 + job_id), poll GET /query/ask/jobs/{id} until it finishes.
 * Resolves with the same response shape as the synchronous ask.
 */
import { apiRequest } from './api';

export interface AskJobStatus {
  status: 'queued' | 'running';
  queuePosition?: number;
}

export interface AskQuestionOptions {
  /** Called on each poll while the question is queued/running. */
  onStatus?: (status: AskJobStatus) => void;
  pollIntervalMs?: number;
  timeoutMs?: number;
}

const DEFAULT_POLL_INTERVAL_MS = 2500;
const DEFAULT_TIMEOUT_MS = 10 * 60 * 1000;

/** Soft-launch copy for gateway / non-JSON failures (never show raw HTML/JSON parse errors). */
const ASK_RETRY_MESSAGE =
  "Your question couldn't be completed. Please try again in a moment.";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function errorDetail(data: unknown, fallback: string): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
  }
  return fallback;
}

function looksLikeHtml(text: string): boolean {
  const t = text.trim().toLowerCase();
  return t.startsWith('<!doctype') || t.startsWith('<html') || t.startsWith('<head');
}

async function readAskJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text.trim()) {
    if (!res.ok || res.status >= 502) {
      throw new Error(ASK_RETRY_MESSAGE);
    }
    return {};
  }
  if (looksLikeHtml(text)) {
    throw new Error(ASK_RETRY_MESSAGE);
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new Error(ASK_RETRY_MESSAGE);
  }
}

async function pollAskJob(jobId: string, options: AskQuestionOptions): Promise<any> {
  const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const deadline = Date.now() + (options.timeoutMs ?? DEFAULT_TIMEOUT_MS);

  while (Date.now() < deadline) {
    await sleep(pollIntervalMs);
    const res = await apiRequest(`/api/v1/query/ask/jobs/${jobId}`);
    const data = await readAskJson(res);
    if (!res.ok) {
      throw new Error(errorDetail(data, ASK_RETRY_MESSAGE));
    }
    if (data && typeof data === 'object' && (data as { status?: string }).status === 'done') {
      return (data as { result?: unknown }).result;
    }
    if (data && typeof data === 'object' && (data as { status?: string }).status === 'error') {
      const err = (data as { error?: unknown }).error;
      throw new Error(typeof err === 'string' && err ? err : ASK_RETRY_MESSAGE);
    }
    const status = data && typeof data === 'object' ? (data as { status?: string; queue_position?: number }) : {};
    options.onStatus?.({
      status: status.status === 'running' ? 'running' : 'queued',
      queuePosition: typeof status.queue_position === 'number' ? status.queue_position : undefined,
    });
  }
  throw new Error(ASK_RETRY_MESSAGE);
}

/**
 * Submit a question and resolve with the ask response
 * ({ prepared, clauses, retrieval, answer, retry, trace }).
 * Handles both the synchronous path and the queued (202 + poll) path.
 */
export async function askQuestion(
  payload: Record<string, unknown>,
  options: AskQuestionOptions = {}
): Promise<any> {
  try {
    const res = await apiRequest('/api/v1/query/ask', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const data = await readAskJson(res);

    if (
      res.status === 202 &&
      data &&
      typeof data === 'object' &&
      (data as { job_id?: string }).job_id
    ) {
      options.onStatus?.({
        status: 'queued',
        queuePosition:
          typeof (data as { queue_position?: number }).queue_position === 'number'
            ? (data as { queue_position: number }).queue_position
            : undefined,
      });
      return pollAskJob((data as { job_id: string }).job_id, options);
    }

    if (!res.ok) {
      // Rate-limit / business errors may still have a useful detail string
      const detail = errorDetail(data, ASK_RETRY_MESSAGE);
      if (res.status === 429) {
        throw new Error(detail);
      }
      if (res.status >= 500 || res.status === 502 || res.status === 503 || res.status === 504) {
        throw new Error(ASK_RETRY_MESSAGE);
      }
      throw new Error(detail);
    }
    return data;
  } catch (err) {
    if (err instanceof Error) {
      const msg = err.message || '';
      if (
        msg.includes('Unexpected token') ||
        msg.includes('is not valid JSON') ||
        msg.includes('Failed to fetch') ||
        msg.includes('NetworkError') ||
        msg.includes('Load failed')
      ) {
        throw new Error(ASK_RETRY_MESSAGE);
      }
      throw err;
    }
    throw new Error(ASK_RETRY_MESSAGE);
  }
}
