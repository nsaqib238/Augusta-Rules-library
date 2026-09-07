import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { supabase } from '../../lib/supabase';
import { apiRequest, withApiBase } from '../../lib/api';
import {
  Discipline,
  OTHER_CODEBOOK,
  codebooksForUserUploads,
  defaultUserCodebookForDiscipline,
  disciplineMeta,
  labelForCodebookId,
} from '../../lib/codebooks';
import { formatDocumentStatus, documentStatusBadgeClass, isDocumentProcessing, isDocumentReadyForSearch } from '../../lib/uploadDisplay';
import { typography } from '../../styles/typography';
import AdminRagDebugPanel from './AdminRagDebugPanel';

interface LabDocument {
  id: string;
  filename: string;
  codebook: string;
  source?: string;
  discipline?: string;
  status: string;
  file_size: number;
  created_at: string;
  refined_chunk_count?: number;
}

const MAX_UPLOAD_MB = 80;

const AdminRagLabPanel: React.FC = () => {
  const { user } = useAuth();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [discipline, setDiscipline] = useState<Discipline>('electrical');
  const [codebook, setCodebook] = useState('AS3000');
  const [customCodebook, setCustomCodebook] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState('');
  const [uploadError, setUploadError] = useState('');
  const [documents, setDocuments] = useState<LabDocument[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [ragResetKey, setRagResetKey] = useState('0');
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [selectedDocumentName, setSelectedDocumentName] = useState('');
  const [selectedCodebook, setSelectedCodebook] = useState('');
  const [selectedCodebookSource, setSelectedCodebookSource] = useState('');

  const info = disciplineMeta(discipline);
  const codebookOptions = codebooksForUserUploads(discipline);

  const fetchDocuments = useCallback(async () => {
    if (!user?.id) return;
    setLoadingDocs(true);
    try {
      const { data, error } = await supabase
        .from('documents')
        .select('id, filename, codebook, source, discipline, status, file_size, created_at, refined_chunk_count')
        .eq('user_id', user.id)
        .order('created_at', { ascending: false });

      if (error) throw error;
      setDocuments(data || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingDocs(false);
    }
  }, [user?.id]);

  useEffect(() => {
    void fetchDocuments();
  }, [fetchDocuments]);

  useEffect(() => {
    if (codebook === OTHER_CODEBOOK) return;
    setCodebook(defaultUserCodebookForDiscipline(discipline));
  }, [discipline]);

  const readyForCodebook = documents.filter(
    (d) => d.codebook === codebook && d.status === 'ready_for_search'
  );
  const processingCount = documents.filter((d) => isDocumentProcessing(d.status)).length;

  useEffect(() => {
    if (processingCount === 0) return;
    const t = setInterval(() => void fetchDocuments(), 20000);
    return () => clearInterval(t);
  }, [processingCount, fetchDocuments]);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !user?.id) return;

    setUploading(true);
    setUploadError('');
    setUploadMsg('');

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) throw new Error('Not authenticated');

      const resolvedCodebook = codebook === OTHER_CODEBOOK ? customCodebook.trim() : codebook;
      if (!resolvedCodebook) throw new Error('Select or enter a codebook');

      const formData = new FormData();
      formData.append('file', file);
      formData.append('discipline', discipline);
      formData.append('codebook', resolvedCodebook);
      if (codebook === OTHER_CODEBOOK) {
        formData.append('source', customCodebook.trim());
      } else {
        formData.append('source', labelForCodebookId(codebook));
      }

      const response = await fetch(withApiBase('/api/v1/uploads/'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
        body: formData,
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || data.message || 'Upload failed');
      }

      setUploadMsg(data.message || 'PDF uploaded — processing started.');
      setFile(null);
      await fetchDocuments();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleSelectDocument = (doc: LabDocument) => {
    if (!isDocumentReadyForSearch(doc.status)) return;
    setSelectedDocumentId(doc.id);
    setSelectedDocumentName(doc.filename);
    setSelectedCodebook(doc.codebook);
    setSelectedCodebookSource(doc.source || '');
    setCodebook(doc.codebook);
    if (doc.discipline) setDiscipline(doc.discipline as Discipline);
  };

  const syncEmbeddings = async (doc: LabDocument) => {
    if (!user?.id) return;
    setSyncingId(doc.id);
    try {
      const res = await apiRequest(
        `/api/v1/admin/documents/${doc.id}/sync-embeddings?user_id=${user.id}`,
        { method: 'POST' }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Sync failed');
      setUploadMsg(`Embeddings synced for ${doc.filename}: ${data.embeddings_upserted ?? 0} rows`);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Embedding sync failed');
    } finally {
      setSyncingId(null);
    }
  };

  return (
    <div className="rounded-[26px] bg-gradient-to-br from-white/70 to-[#fff7e3]/35">
      <div className="mx-auto max-w-7xl px-2 py-2 sm:px-4 sm:py-4">
        <div className="mb-6 rounded-[26px] border border-white/70 bg-white/75 p-6 shadow-sm backdrop-blur">
          <p className="augusta-eyebrow mb-3">Admin R&D workspace</p>
          <h1 className={`${typography.sectionTitle} mb-2 flex items-center gap-2 tracking-tight text-slate-950`}>
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#0b1220] text-lg shadow-lg shadow-slate-900/20">
              🧪
            </span>
            RAG Search Lab
          </h1>
          <p className={`${typography.helper} max-w-3xl`}>
            Upload a test PDF, wait for ingest, then run the same prepare → multi-signal search → clause list →
            answer pipeline as the user Q&A tabs. Use this to tune retrieval and inspect what the LLM sees.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
          {/* Upload & document library */}
          <div className="space-y-4 lg:col-span-1">
            <div className="rounded-[26px] border border-white/70 bg-white/80 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#9a7a35]">Step 1</p>
              <h2 className="mt-1 text-lg font-semibold text-slate-950">Upload test PDF</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                PDFs upload to your admin account. Search runs against your ready documents for the selected codebook.
              </p>

              <form onSubmit={handleUpload} className="mt-4 space-y-3">
                <label className="block text-xs font-semibold uppercase tracking-widest text-slate-500">
                  Discipline
                  <select
                    className="augusta-input mt-1 w-full"
                    value={discipline}
                    onChange={(e) => setDiscipline(e.target.value as Discipline)}
                  >
                    <option value="electrical">Electrical</option>
                    <option value="mechanical">Mechanical</option>
                    <option value="fire">Fire</option>
                    <option value="hydraulics">Hydraulics</option>
                  </select>
                </label>

                <label className="block text-xs font-semibold uppercase tracking-widest text-slate-500">
                  Codebook
                  <select
                    className="augusta-input mt-1 w-full"
                    value={codebook}
                    onChange={(e) => setCodebook(e.target.value)}
                  >
                    {codebookOptions.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                    <option value={OTHER_CODEBOOK}>Other (custom)</option>
                  </select>
                </label>

                {codebook === OTHER_CODEBOOK && (
                  <input
                    className="augusta-input w-full"
                    value={customCodebook}
                    onChange={(e) => setCustomCodebook(e.target.value)}
                    placeholder="Custom code id"
                    required
                  />
                )}

                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  className="hidden"
                  onChange={(ev) => setFile(ev.target.files?.[0] || null)}
                />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="w-full rounded-[20px] border border-dashed border-[#d6bf82]/80 bg-[#fffaf1]/70 px-3 py-4 text-center text-xs text-slate-600 hover:border-[#c9a45c]"
                >
                  {file ? file.name : `Choose PDF (max ${MAX_UPLOAD_MB} MB)`}
                </button>

                <button
                  type="submit"
                  disabled={!file || uploading}
                  className="augusta-button-primary w-full py-2.5 disabled:opacity-50"
                >
                  {uploading ? 'Uploading…' : 'Upload & process'}
                </button>
              </form>

              {(uploadMsg || uploadError) && (
                <div
                  className={`mt-3 rounded-2xl p-3 text-xs ${
                    uploadError ? 'border border-red-100 bg-red-50 text-red-700' : 'border border-green-100 bg-green-50 text-green-700'
                  }`}
                >
                  {uploadError || uploadMsg}
                </div>
              )}
            </div>

            <div className="rounded-[26px] border border-white/70 bg-white/80 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#9a7a35]">Library</p>
                  <h2 className="mt-1 text-base font-semibold text-slate-950">Your test documents</h2>
                </div>
                <button
                  type="button"
                  onClick={() => void fetchDocuments()}
                  className="rounded-full border border-[#f1ddab]/70 bg-[#fffaf1] px-2 py-1 text-[10px] font-semibold text-[#7c5f1e]"
                >
                  Refresh
                </button>
              </div>

              <p className="mt-2 text-xs text-slate-500">
                Click a <span className="font-medium text-emerald-700">ready</span> document to scope search. Ready for{' '}
                <span className="font-medium text-slate-700">{codebook}</span>:{' '}
                <span className="font-semibold text-emerald-700">{readyForCodebook.length}</span>
              </p>

              {loadingDocs ? (
                <div className="mt-4 flex justify-center py-6">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]" />
                </div>
              ) : documents.length === 0 ? (
                <p className="mt-4 rounded-2xl border border-dashed border-slate-200 py-6 text-center text-xs text-slate-400">
                  No documents yet — upload a PDF above.
                </p>
              ) : (
                <ul className="mt-3 max-h-[320px] space-y-2 overflow-y-auto">
                  {documents.slice(0, 12).map((doc) => {
                    const selectable = isDocumentReadyForSearch(doc.status);
                    return (
                    <li
                      key={doc.id}
                      className={`rounded-2xl border p-3 text-xs transition-all ${
                        selectable ? 'cursor-pointer' : 'cursor-not-allowed opacity-80'
                      } ${
                        selectedDocumentId === doc.id
                          ? 'border-[#c9a45c] bg-[#fff7df] shadow-[0_8px_24px_rgba(201,164,92,0.15)]'
                          : doc.codebook === codebook && doc.status === 'ready_for_search'
                            ? 'border-emerald-200 bg-emerald-50/50'
                            : 'border-slate-200 bg-white'
                      }`}
                      onClick={() => handleSelectDocument(doc)}
                    >
                      <div className="truncate font-semibold text-slate-900">{doc.filename}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-slate-500">
                        <span>{doc.codebook}</span>
                        <span className={`rounded-full px-2 py-0.5 font-semibold ${documentStatusBadgeClass(doc.status)}`}>
                          {formatDocumentStatus(doc.status)}
                        </span>
                      </div>
                      {doc.status === 'ready_for_search' && (
                        <button
                          type="button"
                          disabled={syncingId === doc.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            void syncEmbeddings(doc);
                          }}
                          className="mt-2 text-[10px] font-semibold text-indigo-700 hover:text-indigo-900 disabled:opacity-50"
                        >
                          {syncingId === doc.id ? 'Syncing embeddings…' : 'Sync embeddings'}
                        </button>
                      )}
                    </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          {/* RAG pipeline debugger */}
          <div className="lg:col-span-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#9a7a35]">Step 2</p>
                <h2 className="mt-1 flex items-center gap-2 text-lg font-semibold text-slate-950">
                  <span>{info.icon}</span>
                  Test & inspect pipeline
                </h2>
              </div>
              {readyForCodebook.length === 0 && (
                <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800">
                  No ready docs for {codebook} — upload & wait for processing
                </span>
              )}
              <button
                type="button"
                className="augusta-button-secondary text-xs"
                onClick={() => {
                  setRagResetKey(String(Date.now()));
                  setSelectedDocumentId('');
                  setSelectedDocumentName('');
                  setSelectedCodebook('');
                  setSelectedCodebookSource('');
                }}
              >
                Clear results
              </button>
            </div>

            <AdminRagDebugPanel
              discipline={discipline}
              selectedDocumentId={selectedDocumentId}
              selectedDocumentName={selectedDocumentName}
              selectedCodebook={selectedCodebook}
              selectedCodebookSource={selectedCodebookSource}
              resetKey={ragResetKey}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminRagLabPanel;
