const DOCUMENT_STATUS_LABELS: Record<string, string> = {
  ready_for_search: 'Ready for search',
  pending_admin_review: 'Processing',
  admin_processing: 'Processing',
  pdf_processing: 'Processing PDF',
  failed: 'Failed',
};

/** User-facing document row status (DB values may still use legacy names). */
export function formatDocumentStatus(raw: string): string {
  const s = (raw || '').trim();
  if (DOCUMENT_STATUS_LABELS[s]) return DOCUMENT_STATUS_LABELS[s];
  return s.replace(/_/g, ' ');
}

export function documentStatusBadgeClass(status: string): string {
  switch ((status || '').trim()) {
    case 'ready_for_search':
      return 'bg-emerald-50 text-emerald-700';
    case 'failed':
      return 'bg-red-50 text-red-700';
    case 'pdf_processing':
    case 'admin_processing':
    case 'pending_admin_review':
      return 'bg-[#fff7df] text-[#7c5f1e]';
    default:
      return 'bg-slate-100 text-slate-700';
  }
}

export function isDocumentProcessing(status: string): boolean {
  return ['pdf_processing', 'admin_processing', 'pending_admin_review'].includes(
    (status || '').trim()
  );
}

export function isDocumentReadyForSearch(status: string): boolean {
  return (status || '').trim() === 'ready_for_search';
}

export const FAILED_DOCUMENT_HINT =
  'Processing failed. Remove this file and upload again.';

/** Normalize older progress API strings for in-flight uploads. */
export function humanizeUploadProgressMessage(msg: string): string {
  if (!msg) return msg;
  return msg.replace(/Adding to admin processing queue/gi, 'Queued for processing pipeline');
}
