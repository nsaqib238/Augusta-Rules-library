import React, { useState, useEffect, useRef } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../contexts/AuthContext';
import { Trash2, Clock } from 'lucide-react';
import { withApiBase } from '../lib/api';
import { supportMailto } from '../lib/appConfig';
import { formatDocumentStatus, documentStatusBadgeClass, isDocumentProcessing, FAILED_DOCUMENT_HINT } from '../lib/uploadDisplay';
import {
  OTHER_CODEBOOK,
  inferFamilyFromCodebookId,
} from '../lib/codebooks';
import { fetchVisibleDocuments } from '../lib/companyDocuments';

interface Document {
  id: string;
  filename: string;
  original_filename?: string;
  codebook: string;
  source?: string;
  discipline?: string;
  status: string;
  admin_status: string;
  file_size: number;
  created_at: string;
  admin_notes?: string;
  refined_chunk_count?: number;
  admin_queue?: {
    status: string;
  };
}

/** Must stay in sync with backend `MAX_FILE_SIZE` in `api/v1/uploads.py`. */
const MAX_UPLOAD_MB = 80;
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

const DocumentUpload: React.FC = () => {
  const { user } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [discipline, setDiscipline] = useState<string>('electrical');
  const [codeName, setCodeName] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [licenceConfirmed, setLicenceConfirmed] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (user) {
      fetchDocuments();
    }
  }, [user]);

  useEffect(() => {
    setCodeName('');
  }, [discipline]);

  const hasProcessingDocs = documents.some((doc) => isDocumentProcessing(doc.status));

  useEffect(() => {
    if (!user || !hasProcessingDocs) return;
    const interval = setInterval(() => {
      fetchDocuments();
    }, 30000);
    return () => clearInterval(interval);
  }, [user, hasProcessingDocs]);

  const fetchDocuments = async () => {
    try {
      console.log('🔍 DocumentUpload - Fetching documents for user:', user?.id);
      
      if (!user?.id) {
        throw new Error('No user ID available');
      }
      
      const { data: { session } } = await supabase.auth.getSession();
      console.log('🔍 DocumentUpload - Session data:', session);
      
      if (!session) {
        throw new Error('No active session');
      }

      // Own uploads + shared company library (RLS allows company peers)
      const documentsData = await fetchVisibleDocuments(user.id);

      // Fetch admin queue status for each document
      const documentsWithAdminStatus = await Promise.all(
        (documentsData || []).map(async (doc) => {
          const { data: adminQueueData } = await supabase
            .from('admin_queue')
            .select('status')
            .eq('document_id', doc.id as string)
            .maybeSingle();
          
          return {
            ...(doc as unknown as Document),
            admin_queue: adminQueueData ? { status: adminQueueData.status } : { status: 'pending' }
          };
        })
      );

      console.log('🔍 DocumentUpload - Documents loaded with admin status:', documentsWithAdminStatus);
      setDocuments(documentsWithAdminStatus);
      
    } catch (err) {
      console.error('🔍 DocumentUpload - Error:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch documents');
    } finally {
      setLoading(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      if (selectedFile.type !== 'application/pdf') {
        setError('Please select a PDF file');
        return;
      }
      if (selectedFile.size > MAX_UPLOAD_BYTES) {
        setError(`File size must be ${MAX_UPLOAD_MB}MB or less`);
        return;
      }
      setFile(selectedFile);
      setError('');
    }
  };

  const codeNameReady = codeName.trim().length > 0;

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !user) return;
    if (!licenceConfirmed) {
      setError('Confirm you hold a valid licence to use this document before uploading.');
      return;
    }
    if (!codeName.trim()) {
      setError('Enter the code or standard name before uploading.');
      return;
    }

    setUploading(true);
    setProgress(0);
    setMessage('');
    setError('');

    try {
      // Get JWT token for API calls
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      const trimmedName = codeName.trim();
      const inferredFamily = inferFamilyFromCodebookId(trimmedName, trimmedName);

      const formData = new FormData();
      formData.append('file', file);
      formData.append('user_id', session.user.id);
      formData.append('discipline', discipline);
      formData.append('standard_family', inferredFamily);
      formData.append('codebook', OTHER_CODEBOOK);
      formData.append('codebook_custom', trimmedName);

      const UPLOAD_PROCESS_TIMEOUT_MS = 3 * 60 * 60 * 1000;
      const controller = new AbortController();
      const t = window.setTimeout(() => controller.abort(), UPLOAD_PROCESS_TIMEOUT_MS);

      const response = await fetch(withApiBase('/api/v1/uploads/'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
        body: formData,
        signal: controller.signal,
      });
      window.clearTimeout(t);

      const rawResponseText = await response.text();
      let parsedResponse: any = null;
      const looksLikeHtml = /^\s*<(?:!doctype|html|head|body)/i.test(rawResponseText);

      if (rawResponseText && !looksLikeHtml) {
        try {
          parsedResponse = JSON.parse(rawResponseText);
        } catch (parseError) {
          console.warn('⚠️ Unable to parse upload response as JSON:', parseError);
        }
      }

      if (!response.ok) {
        // 502/503/504 from Nginx come back as HTML. Surface a useful message
        // and tell the user their upload may still be processing in the background.
        if (looksLikeHtml || response.status === 502 || response.status === 503 || response.status === 504) {
          throw new Error(
            response.status === 504
              ? 'The server took too long to confirm your upload. It may still be processing — refresh "Recently Uploaded" in a minute.'
              : 'The server is busy right now (gateway error). Please wait a moment and try again — refresh "Recently Uploaded" to see if it landed.'
          );
        }
        const errorMessage =
          typeof parsedResponse === 'object' && parsedResponse !== null
            ? parsedResponse.detail || parsedResponse.message || 'Upload failed'
            : rawResponseText || response.statusText || 'Upload failed';
        throw new Error(errorMessage);
      }

      if (parsedResponse && typeof parsedResponse === 'object') {
        setMessage(
          typeof parsedResponse.message === 'string' && parsedResponse.message
            ? parsedResponse.message
            : `Document uploaded. Status: ${parsedResponse.status || 'ok'}`
        );
      } else {
        setMessage('Document uploaded successfully!');
      }
      setFile(null);
      setLicenceConfirmed(false);
      setCodeName('');
      setProgress(100);
      
      // Refresh the document list
      await fetchDocuments();
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setError('Upload/processing timed out after 3 hours. Check your documents list; processing may still complete on the server.');
      } else {
        setError(err instanceof Error ? err.message : 'Upload failed');
      }
      // Best-effort refresh: even on a 504 the document row may have been
      // created on the server, so updating the list lets the user see it.
      try {
        await fetchDocuments();
      } catch {
        /* ignore */
      }
    } finally {
      setUploading(false);
    }
  };

  const deleteDocument = async (documentId: string) => {
    // Find the document to get its filename for confirmation
    const document = documents.find(doc => doc.id === documentId);
    const filename = document?.filename || 'this document';
    const isFailed = document?.status === 'failed';

    const confirmed = window.confirm(
      isFailed
        ? `Remove failed upload "${filename}"?\n\n${FAILED_DOCUMENT_HINT}`
        : `Are you sure you want to delete "${filename}"?\n\nThis action cannot be undone and will permanently remove the document and all its associated chunks.`
    );
    
    if (!confirmed) {
      return;
    }

    try {
      console.log(`🔍 Starting deletion of document: ${documentId}`);
      
      setDeleting(documentId);
      setError('');
      
      // Get current user
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) {
        throw new Error('No authenticated user');
      }

      console.log(`🔍 User found: ${user.id}`);
      console.log(`🔍 Calling backend API: /api/v1/documents/${documentId}?user_id=${user.id}`);

      // Get JWT token for API calls
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      // Call backend API to delete document
      const response = await fetch(`/api/v1/documents/${documentId}?user_id=${user.id}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
      });

      if (response.ok) {
        console.log(`✅ Document deleted successfully: ${documentId}`);
        
        // Update local state
        setDocuments(prev => prev.filter(doc => doc.id !== documentId));
        setDeleting(null);
        setError('');
        setMessage(`Document "${filename}" deleted successfully!`);
      } else {
        const errorData = await response.json();
        console.error(`❌ Failed to delete document: ${errorData.detail || 'Unknown error'}`);
        setError(`Failed to delete document: ${errorData.detail || 'Unknown error'}`);
        setDeleting(null);
      }
      
    } catch (error) {
      console.error('❌ Error deleting document:', error);
      setError(`Error deleting document: ${error instanceof Error ? error.message : 'Unknown error'}`);
      setDeleting(null);
    }
  };

  const handleDropzoneClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="w-full rounded-[28px] bg-gradient-to-br from-white/70 to-[#fff7e3]/35 px-2 py-2 sm:px-4 sm:py-4">
      <div className="max-w-6xl mx-auto space-y-8">
        <div className="rounded-[26px] border border-white/70 bg-white/75 p-6 shadow-sm backdrop-blur">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <p className="augusta-eyebrow mb-3">Document Pipeline</p>
            <h1 className="text-xl font-semibold tracking-tight text-slate-950">Upload codes &amp; standards</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
              Upload a PDF you are licensed to use. Name the code or standard so it stays organised in your library.
            </p>
          </div>
          </div>
        </div>

        <div className="grid lg:grid-cols-[3fr,2fr] gap-6">
          <div className="rounded-[28px] border border-white/70 bg-white/82 p-6 shadow-[0_22px_70px_rgba(15,23,42,0.10)] backdrop-blur-xl sm:p-7">
            <div className="flex items-center justify-between mb-6">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#9a7a35]">Step 1 of 2</p>
                <h2 className="mt-1 text-lg font-semibold text-slate-950">Upload Document</h2>
                <p className="text-sm text-slate-500">Choose a discipline, name the code, and upload a PDF you are licensed to use.</p>
                <p className="mt-1 text-xs text-slate-500">
                  Scanned or image-only PDFs are not accepted. Use a digital PDF from the publisher, or one that has already been OCR&apos;d.
                </p>
              </div>
              <span className="rounded-full border border-[#f1ddab]/70 bg-[#fffaf1] px-3 py-1 text-xs font-semibold text-[#7c5f1e]">Digital PDF only</span>
            </div>

            <form onSubmit={handleUpload} className="space-y-5">
              <div>
                <label htmlFor="discipline" className="text-xs font-semibold uppercase tracking-widest text-slate-500">
                  Discipline
                </label>
                <div className="mt-2">
                  <select
                    id="discipline"
                    value={discipline}
                    onChange={(e) => setDiscipline(e.target.value)}
                    className="w-full rounded-2xl border border-slate-200 bg-white/90 px-4 py-3 text-sm font-medium text-slate-700 outline-none transition focus:border-[#c9a45c] focus:ring-4 focus:ring-[#f1ddab]/45"
                  >
                    <option value="electrical">⚡ Electrical</option>
                    <option value="mechanical">❄ Mechanical (HVAC)</option>
                    <option value="fire">🔥 Fire Safety</option>
                    <option value="hydraulics">💧 Hydraulics</option>
                  </select>
                </div>
              </div>

              <div>
                <label htmlFor="codeName" className="text-xs font-semibold uppercase tracking-widest text-slate-500">
                  Code / standard name
                </label>
                <div className="mt-2 space-y-2">
                  <input
                    id="codeName"
                    type="text"
                    value={codeName}
                    onChange={(e) => setCodeName(e.target.value)}
                    placeholder="Enter the code or standard name as it appears on your PDF"
                    className="w-full rounded-2xl border border-slate-200 bg-white/90 px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-[#c9a45c] focus:ring-4 focus:ring-[#f1ddab]/45"
                    required
                  />
                  <p className="text-xs text-slate-500">
                    Use the title from your licensed document. Upload is disabled until you enter a name.
                  </p>
                </div>
              </div>

              <div className="space-y-3">
                <label className="text-xs font-semibold uppercase tracking-widest text-slate-500">
                  Select PDF document
                </label>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  onChange={handleFileChange}
                  className="hidden"
                />
                <div
                  onClick={handleDropzoneClick}
                  className="cursor-pointer rounded-[24px] border border-dashed border-[#d6bf82]/80 bg-[#fffaf1]/70 p-8 text-center transition hover:-translate-y-0.5 hover:border-[#c9a45c] hover:bg-[#fff7df]"
                >
                  <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-3xl bg-[#0b1220] text-lg text-white shadow-lg shadow-slate-900/20">
                    ↑
                  </div>
                  <p className="text-sm font-semibold text-slate-950">Drop your codes or standards PDF here</p>
                  <p className="text-xs text-slate-500">or click to browse · Max {MAX_UPLOAD_MB} MB · Digital PDF only · no scans</p>
                </div>
                {file && (
                  <div className="inline-flex items-center gap-2 rounded-2xl border border-[#f1ddab]/70 bg-[#fffaf1] px-3 py-2 text-xs text-[#7c5f1e]">
                    <span className="font-medium">{file.name}</span>
                    <span>({(file.size / 1024 / 1024).toFixed(2)} MB)</span>
                  </div>
                )}
              </div>

              <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={licenceConfirmed}
                  onChange={(e) => setLicenceConfirmed(e.target.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-[#9a7a35] focus:ring-[#c9a45c]"
                />
                <span>
                  I confirm I hold a valid licence or entitlement to use this document and am authorised to upload it
                  for private use in my Augusta Search account (or company library).
                </span>
              </label>

              <button
                type="submit"
                disabled={!file || uploading || !codeNameReady || !licenceConfirmed}
                className="augusta-button-primary flex w-full items-center justify-center gap-2 py-3"
              >
                {uploading ? 'Uploading…' : 'Upload & start processing'}
              </button>
            </form>

            {uploading && (
              <div className="mt-6">
                <div className="h-2 w-full rounded-full bg-slate-100">
                  <div
                    className="h-2 rounded-full bg-gradient-to-r from-[#c9a45c] to-[#0b1220] transition-all duration-300"
                    style={{ width: `${progress}%` }}
                  ></div>
                </div>
                <p className="mt-2 text-xs text-slate-500">Uploading… {progress}%</p>
              </div>
            )}

            {(message || error) && (
              <div className="mt-4 space-y-2">
                {message && (
                  <div className="p-3 text-sm rounded-2xl bg-green-50 border border-green-100 text-green-700">
                    {message}
                  </div>
                )}
                {error && (
                  <div className="p-3 text-sm rounded-2xl bg-red-50 border border-red-100 text-red-700">
                    {error}
                  </div>
                )}
              </div>
            )}

          </div>

          <div className="space-y-6">
            <div className="rounded-[28px] border border-white/70 bg-white/82 p-6 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur-xl sm:p-7">
              <h2 className="text-base font-semibold tracking-tight text-slate-950">What happens after upload?</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
                Your PDF is stored securely and processed automatically in the background—clause extraction, tables, and
                search indexing run end-to-end in the cloud.
              </p>

              <div className="mt-5 rounded-2xl border border-[#f1ddab]/70 bg-[#fffaf1]/80 px-4 py-3.5">
                <div className="flex gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm ring-1 ring-[#f1ddab]/80">
                    <Clock className="h-4 w-4 text-[#9a7a35]" aria-hidden />
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-[#9a7a35]">Processing time</p>
                    <p className="text-sm text-slate-700 mt-1 leading-relaxed">
                      Depending on file size, layout complexity, and current session load (how many uploads are
                      queued ahead of yours), a full run typically takes{' '}
                      <span className="font-semibold text-slate-900">1–2 hours</span>. Busier periods may take longer.
                      You may leave this page—open{' '}
                      <span className="font-medium text-slate-800">Recently Uploaded</span> anytime to check status.
                    </p>
                  </div>
                </div>
              </div>

              <div className="mt-6 space-y-5">
                {[
                  {
                    title: 'Stored in your secure library',
                    desc: 'Your PDF is saved to private storage and linked to your account with the code name and discipline you entered.',
                  },
                  {
                    title: 'Live automated pipeline',
                    desc: 'Extraction, table handling, and clause-level chunking run in the cloud. Processing continues while you work elsewhere.',
                  },
                  {
                    title: 'Ready for search and Q&A',
                    desc: 'When the job completes, your standard is available for fast clause-level search and grounded answers with references.',
                  },
                ].map((step, idx, arr) => (
                  <div key={step.title} className="flex gap-4">
                    <div className="flex flex-col items-center">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[#f1ddab]/80 bg-[#fff7df] text-xs font-semibold text-[#7c5f1e]">
                        {idx + 1}
                      </div>
                      {idx < arr.length - 1 && (
                        <div className="mt-1 min-h-[1.25rem] w-px flex-1 bg-gradient-to-b from-[#f1ddab] via-[#fff7df] to-transparent" />
                      )}
                    </div>
                    <div className="pb-0.5">
                      <p className="text-sm font-semibold text-gray-900">{step.title}</p>
                      <p className="text-xs text-gray-600 mt-1 leading-relaxed">{step.desc}</p>
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-6 flex flex-col gap-3 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
                <span className="text-xs text-gray-500">Need help uploading?</span>
                <a
                  href={supportMailto('Upload help')}
                  className="text-xs font-semibold text-[#9a7a35] hover:text-[#7c5f1e]"
                >
                  Contact support →
                </a>
              </div>
            </div>

            <div className="rounded-[28px] border border-white/70 bg-white/82 p-6 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur-xl sm:p-7">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#9a7a35]">Your documents</p>
                  <h2 className="mt-1 text-lg font-semibold text-slate-950">Recently Uploaded</h2>
                </div>
                <button
                  className="rounded-full border border-[#f1ddab]/70 bg-[#fffaf1] px-3 py-1 text-xs font-semibold text-[#7c5f1e] hover:bg-[#fff7df]"
                  onClick={fetchDocuments}
                  type="button"
                  disabled={loading}
                >
                  Refresh
                </button>
              </div>

              <div className="mt-4">
                {loading ? (
                  <div className="flex items-center justify-center py-6">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]"></div>
                  </div>
                ) : documents.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-slate-200 bg-white/70 py-8 text-center">
                    <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-3xl bg-[#fff7df] text-2xl text-[#9a7a35] shadow-inner shadow-[#f1ddab]/50">
                      📄
                    </div>
                    <p className="text-sm font-semibold text-slate-950">No documents uploaded yet</p>
                    <p className="mt-1 text-xs text-slate-500">Upload your first licensed codes or standards PDF to see it listed here.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {documents.slice(0, 4).map((doc) => (
                      <div
                        key={doc.id}
                        className="flex items-start gap-3 rounded-2xl border border-white/70 bg-white/85 p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-[#d6bf82]"
                      >
                        <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-[#0b1220] to-[#334155] text-white shadow-lg shadow-slate-900/15">
                          {doc.discipline === 'electrical' ? '⚡' : doc.discipline === 'mechanical' ? '🔧' : doc.discipline === 'fire' ? '🔥' : doc.discipline === 'hydraulics' ? '💧' : '📄'}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <p className="truncate text-sm font-semibold text-slate-950">{doc.filename}</p>
                            <span
                              className={`rounded-full px-3 py-1 text-xs font-semibold ${documentStatusBadgeClass(doc.status)}`}
                            >
                              {formatDocumentStatus(doc.status)}
                            </span>
                          </div>
                          <p className="text-xs text-slate-500 truncate" title={doc.source || doc.codebook}>
                            {doc.source || doc.codebook}
                          </p>
                          <p className="text-xs text-slate-500">
                            {(doc.file_size / 1024 / 1024).toFixed(2)} MB ·{' '}
                            {new Date(doc.created_at).toLocaleDateString('en-US', {
                              year: 'numeric',
                              month: 'short',
                              day: 'numeric',
                            })}
                          </p>
                          {doc.status === 'failed' && (
                            <p className="mt-1 text-xs font-medium text-red-600">{FAILED_DOCUMENT_HINT}</p>
                          )}
                          <div className="flex items-center gap-3 mt-2">
                            <span className="text-xs font-medium text-slate-600">{doc.codebook}</span>
                            <span className="text-xs capitalize text-slate-500">
                              {doc.discipline || 'electrical'}
                            </span>
                            <button
                              onClick={() => deleteDocument(doc.id)}
                              disabled={deleting === doc.id}
                              className="inline-flex items-center gap-1 text-xs font-semibold text-red-500 hover:text-red-700 disabled:opacity-50"
                            >
                              {deleting === doc.id ? (
                                <span className="inline-flex items-center gap-1">
                                  <span className="w-3 h-3 border-2 border-red-500 border-t-transparent rounded-full animate-spin"></span>
                                  Removing…
                                </span>
                              ) : (
                                <>
                                  <Trash2 className="h-3.5 w-3.5" />
                                  Remove
                                </>
                              )}
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                    {documents.length > 4 && (
                      <div className="text-center text-xs text-slate-500">
                        +{documents.length - 4} more document{documents.length - 4 === 1 ? '' : 's'} in your library
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {documents.length > 0 && (
          <div className="rounded-[28px] border border-white/70 bg-white/82 p-6 shadow-[0_22px_70px_rgba(15,23,42,0.10)] backdrop-blur-xl sm:p-7">
            <div className="flex items-center justify-between">
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.28em] text-[#9a7a35]">Library Overview</p>
                <h2 className="text-xl font-semibold text-slate-950">Your Document Inventory</h2>
              </div>
            </div>

            <div className="mt-6 overflow-x-auto rounded-2xl border border-slate-200/70 bg-white/70">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-[#fffaf1] text-left text-xs uppercase tracking-wide text-[#7c5f1e]">
                    <th className="py-3 pr-4">Document</th>
                    <th className="py-3 pr-4">Code</th>
                    <th className="py-3 pr-4">Discipline</th>
                    <th className="py-3 pr-4">Status</th>
                    <th className="py-3 pr-4">Size</th>
                    <th className="py-3 pr-4">Uploaded</th>
                    <th className="py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {documents.map((doc) => (
                    <tr key={doc.id} className="text-slate-700">
                      <td className="py-3 pr-4">
                        <div className="font-medium">{doc.filename}</div>
                        {(doc.source || doc.codebook) && (
                          <div className="text-xs text-slate-500 truncate max-w-xs" title={doc.source || doc.codebook}>
                            {doc.source || doc.codebook}
                          </div>
                        )}
                      </td>
                      <td className="py-3 pr-4 font-mono text-xs">{doc.codebook}</td>
                      <td className="py-3 pr-4 capitalize">{doc.discipline || 'electrical'}</td>
                      <td className="py-3 pr-4">
                        <span
                          className={`rounded-full px-3 py-1 text-xs font-semibold ${documentStatusBadgeClass(doc.status)}`}
                        >
                          {formatDocumentStatus(doc.status)}
                        </span>
                        {doc.status === 'failed' && (
                          <p className="mt-1 text-xs font-medium text-red-600">{FAILED_DOCUMENT_HINT}</p>
                        )}
                      </td>
                      <td className="py-3 pr-4">{(doc.file_size / 1024 / 1024).toFixed(2)} MB</td>
                      <td className="py-3 pr-4">
                        {new Date(doc.created_at).toLocaleDateString('en-US', {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                        })}
                      </td>
                      <td className="py-3 text-right">
                        <button
                          onClick={() => deleteDocument(doc.id)}
                          disabled={deleting === doc.id}
                          className="text-xs font-semibold text-red-500 hover:text-red-700 inline-flex items-center gap-1 disabled:opacity-50"
                        >
                          {deleting === doc.id ? (
                            <span className="inline-flex items-center gap-1">
                              <span className="w-3 h-3 border-2 border-red-500 border-t-transparent rounded-full animate-spin"></span>
                              Removing…
                            </span>
                          ) : (
                            <>
                              <Trash2 className="h-3.5 w-3.5" />
                              Remove
                            </>
                          )}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default DocumentUpload;
