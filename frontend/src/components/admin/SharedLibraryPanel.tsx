import React, { useCallback, useEffect, useState } from 'react';
import { supabase } from '../../lib/supabase';
import { getApiUrl } from '../../lib/api';
import { typography } from '../../styles/typography';

interface EditionRow {
  codebook: string;
  label: string;
  document_id?: string | null;
  chunk_count?: number;
  embedding_count?: number;
  ready?: boolean;
}

interface SharedLibraryPanelProps {
  family: 'SIR' | 'NCC';
  title: string;
  description: string;
}

const SharedLibraryPanel: React.FC<SharedLibraryPanelProps> = ({ family, title, description }) => {
  const apiPrefix = family === 'SIR' ? 'api/v1/admin/sir' : 'api/v1/admin/ncc';
  const editionsPath = `${apiPrefix}/editions`;

  const [editions, setEditions] = useState<EditionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState({
    label: '',
    codebook: '',
    discipline: family === 'SIR' ? 'electrical' : 'fire',
    edition_year: new Date().getFullYear(),
    volume: 'Vol1',
    part: 'All',
  });
  const [uploadingClauses, setUploadingClauses] = useState<string | null>(null);
  const [uploadingTables, setUploadingTables] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [clearing, setClearing] = useState<string | null>(null);
  const [clauseFiles, setClauseFiles] = useState<Record<string, File | null>>({});
  const [tableFiles, setTableFiles] = useState<Record<string, File | null>>({});
  const [lastResult, setLastResult] = useState<Record<string, string>>({});

  const refreshEditions = useCallback(async () => {
    try {
      setLoading(true);
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) return;

      const res = await fetch(getApiUrl(editionsPath), {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!res.ok) return;

      const rows = (await res.json()) as Array<{
        codebook: string;
        display_name?: string;
        label?: string;
        document_id?: string | null;
        chunk_count?: number;
        embedding_count?: number;
        ready?: boolean;
      }>;

      setEditions(
        rows.map((r) => ({
          codebook: r.codebook,
          label: r.label || r.display_name || r.codebook,
          document_id: r.document_id,
          chunk_count: r.chunk_count ?? 0,
          embedding_count: r.embedding_count ?? 0,
          ready: r.ready ?? false,
        }))
      );
    } catch {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }, [editionsPath]);

  useEffect(() => {
    void refreshEditions();
  }, [refreshEditions]);

  const authFetch = async (path: string, init: RequestInit = {}) => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) throw new Error('Not authenticated');
    const res = await fetch(getApiUrl(path), {
      ...init,
      headers: {
        ...(init.headers || {}),
        Authorization: `Bearer ${session.access_token}`,
      },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  };

  const handleUploadClauses = async (codebook: string) => {
    const file = clauseFiles[codebook];
    if (!file) {
      alert('Select a clause CSV first');
      return;
    }
    try {
      setUploadingClauses(codebook);
      const form = new FormData();
      form.append('codebook', codebook);
      form.append('file', file);
      const data = await authFetch(`${apiPrefix}/upload-clauses`, { method: 'POST', body: form });
      setLastResult((prev) => ({
        ...prev,
        [codebook]: `Uploaded ${data.clauses_uploaded} clauses, ${data.embeddings_synced ?? 0} embeddings`,
      }));
      void refreshEditions();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploadingClauses(null);
    }
  };

  const handleUploadTables = async (codebook: string) => {
    const file = tableFiles[codebook];
    if (!file) {
      alert('Select a tables CSV first');
      return;
    }
    try {
      setUploadingTables(codebook);
      const form = new FormData();
      form.append('codebook', codebook);
      form.append('file', file);
      const data = await authFetch(`${apiPrefix}/upload-tables`, { method: 'POST', body: form });
      setLastResult((prev) => ({
        ...prev,
        [codebook]: `Uploaded ${data.tables_uploaded} tables`,
      }));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploadingTables(null);
    }
  };

  const handleSyncEmbeddings = async (codebook: string) => {
    try {
      setSyncing(codebook);
      const form = new FormData();
      form.append('codebook', codebook);
      const data = await authFetch(`${apiPrefix}/sync-embeddings`, { method: 'POST', body: form });
      setLastResult((prev) => ({
        ...prev,
        [codebook]: `Synced ${data.embeddings_synced} embeddings`,
      }));
      void refreshEditions();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Sync failed');
    } finally {
      setSyncing(null);
    }
  };

  const handleClear = async (codebook: string) => {
    if (!window.confirm(`Clear all ${family} data for ${codebook}? This cannot be undone.`)) return;
    try {
      setClearing(codebook);
      await authFetch(`${apiPrefix}/clear?codebook=${encodeURIComponent(codebook)}`, { method: 'DELETE' });
      setLastResult((prev) => ({ ...prev, [codebook]: 'Library cleared' }));
      void refreshEditions();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Clear failed');
    } finally {
      setClearing(null);
    }
  };

  const handleCreateEdition = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createForm.label.trim()) {
      alert('Enter a display name for the edition');
      return;
    }
    try {
      setCreating(true);
      const payload =
        family === 'NCC'
          ? {
              label: createForm.label.trim(),
              codebook: createForm.codebook.trim() || undefined,
              discipline: createForm.discipline,
              edition_year: createForm.edition_year,
              volume: createForm.volume,
              part: createForm.part.trim() || 'All',
            }
          : {
              label: createForm.label.trim(),
              codebook: createForm.codebook.trim() || undefined,
              discipline: createForm.discipline,
              edition_year: createForm.edition_year || undefined,
            };
      await authFetch(`${apiPrefix}/editions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      setShowCreateForm(false);
      setCreateForm({
        label: '',
        codebook: '',
        discipline: family === 'SIR' ? 'electrical' : 'fire',
        edition_year: new Date().getFullYear(),
        volume: 'Vol1',
        part: 'All',
      });
      void refreshEditions();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not create edition');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="augusta-eyebrow mb-2">Shared library</p>
          <h2 className={`${typography.sectionTitle} text-slate-950`}>{title}</h2>
          <p className={`${typography.helper} mt-2 max-w-3xl`}>{description}</p>
        </div>
        <button
          type="button"
          onClick={() => setShowCreateForm((v) => !v)}
          className="rounded-full bg-[#0b1220] px-5 py-2.5 text-sm font-semibold text-white shadow-sm"
        >
          {showCreateForm ? 'Cancel' : '+ Create edition'}
        </button>
      </div>

      {showCreateForm && (
        <div className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm">
          <h3 className="mb-4 text-base font-semibold text-slate-900">
            Create new {family} edition
          </h3>
          <form onSubmit={(e) => void handleCreateEdition(e)} className="grid gap-4 lg:grid-cols-2">
            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Display name
              </label>
              <input
                type="text"
                value={createForm.label}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, label: e.target.value }))}
                placeholder={family === 'NCC' ? 'NCC 2025 Vol 1 — Class 2–9' : 'Queensland SIR 2026'}
                className="augusta-input w-full"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Codebook ID (optional)
              </label>
              <input
                type="text"
                value={createForm.codebook}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, codebook: e.target.value }))}
                placeholder={family === 'NCC' ? 'Auto: NCC2025_VOL1' : 'Auto from display name'}
                className="augusta-input w-full"
              />
            </div>
            {family === 'NCC' ? (
              <>
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Edition year
                  </label>
                  <input
                    type="number"
                    value={createForm.edition_year}
                    onChange={(e) =>
                      setCreateForm((prev) => ({ ...prev, edition_year: Number(e.target.value) }))
                    }
                    className="augusta-input w-full"
                    required
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Volume
                  </label>
                  <select
                    value={createForm.volume}
                    onChange={(e) => setCreateForm((prev) => ({ ...prev, volume: e.target.value }))}
                    className="augusta-input w-full"
                  >
                    <option value="Vol1">Vol 1</option>
                    <option value="Vol2">Vol 2</option>
                    <option value="Vol3">Vol 3</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Part
                  </label>
                  <input
                    type="text"
                    value={createForm.part}
                    onChange={(e) => setCreateForm((prev) => ({ ...prev, part: e.target.value }))}
                    placeholder="All"
                    className="augusta-input w-full"
                  />
                </div>
              </>
            ) : (
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Edition year (optional)
                </label>
                <input
                  type="number"
                  value={createForm.edition_year}
                  onChange={(e) =>
                    setCreateForm((prev) => ({ ...prev, edition_year: Number(e.target.value) }))
                  }
                  className="augusta-input w-full"
                />
              </div>
            )}
            <div className="lg:col-span-2">
              <button
                type="submit"
                disabled={creating}
                className="rounded-full bg-[#0b1220] px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                {creating ? 'Creating…' : 'Create edition'}
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="h-10 w-10 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]" />
        </div>
      ) : (
        <div className="space-y-4">
          {editions.map((edition) => (
            <div
              key={edition.codebook}
              className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm"
            >
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-slate-900">{edition.label}</h3>
                  <p className="text-xs text-slate-500">Codebook: {edition.codebook}</p>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                    edition.ready
                      ? 'bg-green-100 text-green-800'
                      : 'bg-amber-100 text-amber-800'
                  }`}
                >
                  {edition.ready ? 'Ready' : 'Not loaded'}
                </span>
              </div>

              <div className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <div className="text-xs text-slate-500">Chunks</div>
                  <div className="font-semibold">{edition.chunk_count ?? 0}</div>
                </div>
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <div className="text-xs text-slate-500">Embeddings</div>
                  <div className="font-semibold">{edition.embedding_count ?? 0}</div>
                </div>
                <div className="col-span-2 rounded-xl bg-slate-50 px-3 py-2">
                  <div className="text-xs text-slate-500">Document ID</div>
                  <div className="truncate font-mono text-xs">{edition.document_id || '—'}</div>
                </div>
              </div>

              {lastResult[edition.codebook] && (
                <p className="mb-3 text-sm text-green-700">{lastResult[edition.codebook]}</p>
              )}

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Clause CSV
                  </label>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={(e) =>
                      setClauseFiles((prev) => ({
                        ...prev,
                        [edition.codebook]: e.target.files?.[0] ?? null,
                      }))
                    }
                    className="block w-full text-sm"
                  />
                  <button
                    type="button"
                    disabled={uploadingClauses === edition.codebook}
                    onClick={() => void handleUploadClauses(edition.codebook)}
                    className="rounded-full bg-[#0b1220] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                  >
                    {uploadingClauses === edition.codebook ? 'Uploading…' : 'Upload clauses + embed'}
                  </button>
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Tables CSV
                  </label>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={(e) =>
                      setTableFiles((prev) => ({
                        ...prev,
                        [edition.codebook]: e.target.files?.[0] ?? null,
                      }))
                    }
                    className="block w-full text-sm"
                  />
                  <button
                    type="button"
                    disabled={uploadingTables === edition.codebook}
                    onClick={() => void handleUploadTables(edition.codebook)}
                    className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50"
                  >
                    {uploadingTables === edition.codebook ? 'Uploading…' : 'Upload tables'}
                  </button>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={syncing === edition.codebook}
                  onClick={() => void handleSyncEmbeddings(edition.codebook)}
                  className="rounded-full border border-[#c9a45c] px-4 py-2 text-sm font-semibold text-[#7c5f1e] disabled:opacity-50"
                >
                  {syncing === edition.codebook ? 'Syncing…' : 'Re-sync embeddings'}
                </button>
                <button
                  type="button"
                  disabled={clearing === edition.codebook}
                  onClick={() => void handleClear(edition.codebook)}
                  className="rounded-full border border-red-200 px-4 py-2 text-sm font-semibold text-red-700 disabled:opacity-50"
                >
                  {clearing === edition.codebook ? 'Clearing…' : 'Clear library'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export const SIRLibraryPanel: React.FC = () => (
  <SharedLibraryPanel
    family="SIR"
    title="SIR shared library"
    description="Upload clause and table CSVs once. All users can search these editions via Q&A (prepare → search → answer)."
  />
);

export const NCCLibraryPanel: React.FC = () => (
  <SharedLibraryPanel
    family="NCC"
    title="NCC shared library"
    description="Upload NCC clause and table CSVs once. All users can search these volumes via Q&A (prepare → search → answer)."
  />
);

export default SharedLibraryPanel;
