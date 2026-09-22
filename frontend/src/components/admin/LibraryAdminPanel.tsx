import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { supabase } from '../../lib/supabase';
import { getApiUrl } from '../../lib/api';
import { typography } from '../../styles/typography';
import LibraryTree from '../library/LibraryTree';
import {
  LibraryCatalogDocument,
  LibraryTreeData,
  editionsForDocument,
} from '../../lib/libraryCatalog';
import { inferFamilyFromCodebookId, STANDARD_FAMILIES } from '../../lib/codebooks';
import { formatDocumentStatus, isDocumentProcessing } from '../../lib/uploadDisplay';

const LibraryAdminPanel: React.FC = () => {
  const [tree, setTree] = useState<LibraryTreeData>({
    countries: [],
    types: [],
    documents: [],
    editions: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [countryId, setCountryId] = useState('');
  const [typeId, setTypeId] = useState<string | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const [newTypeName, setNewTypeName] = useState('');
  const [newDoc, setNewDoc] = useState({
    title: '',
    discipline: 'Building',
    publisher: 'ABCB',
    priority: 'critical',
  });
  const [editDoc, setEditDoc] = useState({
    title: '',
    discipline: 'Building',
    publisher: 'ABCB',
    priority: 'critical',
  });
  const [newEdition, setNewEdition] = useState({ label: '', codebook: '', edition_year: '' });
  const [pdfFiles, setPdfFiles] = useState<Record<string, File | null>>({});
  const [wordFiles, setWordFiles] = useState<Record<string, File | null>>({});
  const [parserFamily, setParserFamily] = useState<Record<string, string>>({});
  const [clauseFiles, setClauseFiles] = useState<Record<string, File | null>>({});
  const [tableFiles, setTableFiles] = useState<Record<string, File | null>>({});

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
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof body.detail === 'string' ? body.detail : 'Request failed');
    }
    return body;
  };

  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    try {
      if (!opts?.silent) {
        setLoading(true);
        setError(null);
      }
      const data = (await authFetch('api/v1/admin/library/tree')) as LibraryTreeData;
      setTree({
        countries: data.countries || [],
        types: data.types || [],
        documents: data.documents || [],
        editions: data.editions || [],
      });
      setCountryId((prev) => prev || data.countries?.[0]?.id || '');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load library');
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasProcessing = tree.editions.some(
    (ed) => ed.processing || isDocumentProcessing(ed.status || '')
  );

  useEffect(() => {
    if (!hasProcessing) return;
    const interval = window.setInterval(() => {
      void refresh({ silent: true });
    }, 20000);
    return () => window.clearInterval(interval);
  }, [hasProcessing, refresh]);

  const country = tree.countries.find((c) => c.id === countryId);
  const selectedType = tree.types.find((t) => t.id === typeId) || null;
  const selectedDoc = tree.documents.find((d) => d.id === documentId) || null;
  const nccTypeId =
    tree.types.find((t) => t.slug === 'ncc')?.id ||
    tree.types.find((t) => t.country_id === countryId)?.id ||
    tree.types[0]?.id ||
    null;
  const docEditions = useMemo(
    () => (documentId ? editionsForDocument(tree.editions, documentId) : []),
    [tree.editions, documentId]
  );

  useEffect(() => {
    if (!documentId) return;
    const doc = tree.documents.find((d) => d.id === documentId);
    if (!doc) return;
    setEditDoc({
      title: doc.title || '',
      discipline: doc.discipline || 'Building',
      publisher: doc.publisher || 'ABCB',
      priority: doc.priority || 'critical',
    });
    // Only reset the form when switching volumes, not on background refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  const onCountryChange = (id: string) => {
    setCountryId(id);
    setTypeId(null);
    setDocumentId(null);
  };

  const handleAddType = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!country) return;
    try {
      setBusy(true);
      await authFetch(`api/v1/admin/library/countries/${country.code}/types`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newTypeName.trim() }),
      });
      setNewTypeName('');
      setMessage('Document type added');
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not add type');
    } finally {
      setBusy(false);
    }
  };

  const handleAddDocument = async (e: React.FormEvent) => {
    e.preventDefault();
    const resolvedTypeId = typeId || nccTypeId;
    if (!country || !resolvedTypeId) {
      alert('NCC catalog type is missing. Run combined_setup.sql section 10b, then refresh.');
      return;
    }
    try {
      setBusy(true);
      const created = await authFetch('api/v1/admin/library/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          country_code: country.code,
          document_type_id: resolvedTypeId,
          title: newDoc.title.trim(),
          discipline: newDoc.discipline.trim(),
          publisher: newDoc.publisher.trim() || undefined,
          priority: newDoc.priority,
        }),
      });
      setNewDoc({ title: '', discipline: 'Building', publisher: 'ABCB', priority: 'critical' });
      setMessage(`Added ${created.title || 'NCC volume'}`);
      await refresh();
      if (created?.id) {
        setTypeId(created.document_type_id || resolvedTypeId);
        setDocumentId(created.id);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not add document');
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteEdition = async (codebook: string, label: string) => {
    const ok = window.confirm(
      `Delete “${label}”? This removes uploaded clauses, tables, and search data for this edition. This cannot be undone.`
    );
    if (!ok) return;
    try {
      setBusy(true);
      setMessage(null);
      await authFetch(`api/v1/admin/library/editions/${encodeURIComponent(codebook)}`, {
        method: 'DELETE',
      });
      setMessage(`Deleted ${label}`);
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not delete edition');
    } finally {
      setBusy(false);
    }
  };

  const handleRenameDocument = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedDoc) return;
    try {
      setBusy(true);
      await authFetch(`api/v1/admin/library/documents/${selectedDoc.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: editDoc.title.trim(),
          discipline: editDoc.discipline.trim() || 'Building',
          publisher: editDoc.publisher.trim() || undefined,
          priority: editDoc.priority,
        }),
      });
      setMessage(`Renamed to ${editDoc.title.trim()}`);
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not rename volume');
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteDocument = async () => {
    if (!selectedDoc) return;
    const editionCount = docEditions.length;
    const ok = window.confirm(
      editionCount
        ? `Delete “${selectedDoc.title}” and its ${editionCount} edition${editionCount === 1 ? '' : 's'}? This cannot be undone.`
        : `Delete “${selectedDoc.title}”? This cannot be undone.`
    );
    if (!ok) return;
    try {
      setBusy(true);
      setMessage(null);
      await authFetch(`api/v1/admin/library/documents/${selectedDoc.id}`, {
        method: 'DELETE',
      });
      setMessage(`Deleted ${selectedDoc.title}`);
      setDocumentId(null);
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not delete volume');
    } finally {
      setBusy(false);
    }
  };

  const handleHideDocument = async () => {
    if (!selectedDoc) return;
    try {
      setBusy(true);
      await authFetch(`api/v1/admin/library/documents/${selectedDoc.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: selectedDoc.is_active === false }),
      });
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not update document');
    } finally {
      setBusy(false);
    }
  };

  const handleAddEdition = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedDoc) return;
    try {
      setBusy(true);
      await authFetch(`api/v1/admin/library/documents/${selectedDoc.id}/editions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          label: newEdition.label.trim(),
          codebook: newEdition.codebook.trim() || undefined,
          edition_year: newEdition.edition_year ? Number(newEdition.edition_year) : undefined,
        }),
      });
      setNewEdition({ label: '', codebook: '', edition_year: '' });
      setMessage('Edition created — upload a Word file if you have one');
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Could not create edition');
    } finally {
      setBusy(false);
    }
  };

  const uploadPdf = async (codebook: string, label: string) => {
    const word = wordFiles[codebook];
    const pdf = pdfFiles[codebook];
    if (!word && !pdf) {
      alert('Select a Word file for clauses and/or a PDF for Modal tables');
      return;
    }
    const form = new FormData();
    form.append('file', word || pdf!);
    if (word && pdf) form.append('pdf_file', pdf);
    form.append('standard_family', parserFamily[codebook] || inferFamilyFromCodebookId(codebook, label));
    form.append('replace_existing', 'true');
    try {
      setBusy(true);
      const data = await authFetch(`api/v1/admin/library/editions/${encodeURIComponent(codebook)}/upload-pdf`, {
        method: 'POST',
        body: form,
      });
      setMessage(data.message || 'Upload accepted — extracting');
      setWordFiles((prev) => ({ ...prev, [codebook]: null }));
      setPdfFiles((prev) => ({ ...prev, [codebook]: null }));
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setBusy(false);
    }
  };

  const uploadFor = async (codebook: string, kind: 'clauses' | 'tables') => {
    const file = kind === 'clauses' ? clauseFiles[codebook] : tableFiles[codebook];
    if (!file) {
      alert(`Select a ${kind} CSV first`);
      return;
    }
    const form = new FormData();
    form.append('file', file);
    try {
      setBusy(true);
      const data = await authFetch(`api/v1/admin/library/editions/${encodeURIComponent(codebook)}/upload-${kind}`, {
        method: 'POST',
        body: form,
      });
      setMessage(
        kind === 'clauses'
          ? `Uploaded ${data.clauses_uploaded ?? 0} clauses`
          : `Uploaded ${data.tables_uploaded ?? 0} tables`
      );
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setBusy(false);
    }
  };

  const downloadCsv = async (codebook: string, kind: 'clauses' | 'tables') => {
    try {
      setBusy(true);
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) throw new Error('Not authenticated');
      const res = await fetch(
        getApiUrl(`api/v1/admin/library/editions/${encodeURIComponent(codebook)}/download-${kind}`),
        { headers: { Authorization: `Bearer ${session.access_token}` } }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : `No ${kind} to download`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${codebook}_${kind}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Download failed');
    } finally {
      setBusy(false);
    }
  };

  const renderAddVolumeForm = () => (
    <form onSubmit={handleAddDocument} className="grid gap-3 rounded-2xl border border-dashed border-slate-300 p-4 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
          Add NCC volume
        </label>
        <input
          className="augusta-input w-full"
          value={newDoc.title}
          onChange={(e) => setNewDoc((p) => ({ ...p, title: e.target.value }))}
          placeholder="e.g. NCC Volume One 2018"
          required
        />
      </div>
      <input
        className="augusta-input"
        value={newDoc.discipline}
        onChange={(e) => setNewDoc((p) => ({ ...p, discipline: e.target.value }))}
        placeholder="Discipline"
      />
      <input
        className="augusta-input"
        value={newDoc.publisher}
        onChange={(e) => setNewDoc((p) => ({ ...p, publisher: e.target.value }))}
        placeholder="Publisher"
      />
      <select
        className="augusta-input"
        value={newDoc.priority}
        onChange={(e) => setNewDoc((p) => ({ ...p, priority: e.target.value }))}
      >
        <option value="critical">Critical</option>
        <option value="high">High</option>
        <option value="medium">Medium</option>
      </select>
      <button type="submit" disabled={busy} className="rounded-full bg-[#0b1220] px-5 py-2.5 text-sm font-semibold text-white">
        Add volume
      </button>
    </form>
  );

  const renderCountryPane = () => (
    <div className="space-y-6">
      <div>
        <p className="augusta-eyebrow mb-2">NCC</p>
        <h2 className={`${typography.sectionTitle} text-slate-950`}>
          {country?.name || 'NCC — National Construction Code of Australia'}
        </h2>
        <p className={`${typography.helper} mt-2`}>
          Add volumes such as NCC 2018, 2019, 2022 or 2025 using the form above. Click a volume to rename it, delete it,
          or add a year edition.
        </p>
      </div>
      <details>
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
          Add a grouping (optional)
        </summary>
        <form onSubmit={handleAddType} className="mt-3 flex flex-wrap items-end gap-3">
          <div className="min-w-[220px] flex-1">
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
              New document type
            </label>
            <input
              className="augusta-input w-full"
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
              placeholder="e.g. NCC volumes"
              required
            />
          </div>
          <button type="submit" disabled={busy} className="rounded-full bg-[#0b1220] px-5 py-2.5 text-sm font-semibold text-white">
            Add type
          </button>
        </form>
      </details>
    </div>
  );

  const renderTypePane = () => (
    <div className="space-y-6">
      <div>
        <p className="augusta-eyebrow mb-2">{country?.name}</p>
        <h2 className={`${typography.sectionTitle} text-slate-950`}>{selectedType?.name}</h2>
        <p className={`${typography.helper} mt-2`}>
          Add another NCC volume (2018, 2019, 2022, 2025) using the form above, then open it to upload Word or PDF.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="py-2 pr-3">Title</th>
              <th className="py-2 pr-3">Publisher</th>
              <th className="py-2 pr-3">Priority</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {tree.documents
              .filter((d) => d.document_type_id === typeId && d.country_id === countryId)
              .map((d) => {
                const ready = editionsForDocument(tree.editions, d.id).some((e) => e.ready);
                return (
                  <tr key={d.id} className="border-t border-slate-100">
                    <td className="py-2 pr-3">
                      <button type="button" className="font-medium text-slate-900 underline-offset-2 hover:underline" onClick={() => setDocumentId(d.id)}>
                        {d.title}
                      </button>
                    </td>
                    <td className="py-2 pr-3 text-slate-600">{d.publisher || '—'}</td>
                    <td className="py-2 pr-3 capitalize text-slate-600">{d.priority}</td>
                    <td className="py-2 text-slate-600">{ready ? 'Ready' : 'Empty'}</td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
    </div>
  );

  const renderDocumentPane = (doc: LibraryCatalogDocument) => (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="augusta-eyebrow mb-2">
            {country?.name} / {selectedType?.name || 'NCC volume'}
          </p>
          <h2 className={`${typography.sectionTitle} text-slate-950`}>{doc.title}</h2>
          <p className={`${typography.helper} mt-2`}>
            {doc.discipline}
            {doc.publisher ? ` · ${doc.publisher}` : ''} · {doc.priority}
            {doc.is_active === false ? ' · hidden from users' : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void handleHideDocument()} className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold">
            {doc.is_active === false ? 'Show to users' : 'Hide from users'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleDeleteDocument()}
            className="rounded-full border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 disabled:opacity-50"
          >
            Delete volume
          </button>
        </div>
      </div>

      <form onSubmit={handleRenameDocument} className="grid gap-3 rounded-2xl border border-slate-200/80 bg-white/70 p-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Rename volume</label>
          <input
            className="augusta-input w-full"
            value={editDoc.title}
            onChange={(e) => setEditDoc((p) => ({ ...p, title: e.target.value }))}
            placeholder="e.g. NCC Volume One 2018"
            required
          />
        </div>
        <input
          className="augusta-input"
          value={editDoc.discipline}
          onChange={(e) => setEditDoc((p) => ({ ...p, discipline: e.target.value }))}
          placeholder="Discipline"
        />
        <input
          className="augusta-input"
          value={editDoc.publisher}
          onChange={(e) => setEditDoc((p) => ({ ...p, publisher: e.target.value }))}
          placeholder="Publisher"
        />
        <select
          className="augusta-input"
          value={editDoc.priority}
          onChange={(e) => setEditDoc((p) => ({ ...p, priority: e.target.value }))}
        >
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
        </select>
        <button type="submit" disabled={busy} className="rounded-full bg-[#0b1220] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          Save name
        </button>
      </form>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-slate-900">Editions</h3>
        {docEditions.length === 0 ? (
          <p className="text-sm text-slate-500">No editions yet. Create one, then upload a Word file or PDF.</p>
        ) : (
          docEditions.map((ed) => {
            const family = parserFamily[ed.codebook] || inferFamilyFromCodebookId(ed.codebook, ed.label);
            const processing = Boolean(ed.processing) || isDocumentProcessing(ed.status || '');
            return (
            <div key={ed.codebook} className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium text-slate-900">{ed.label}</p>
                  <p className="text-xs text-slate-500">
                    {ed.codebook} ·{' '}
                    {processing
                      ? formatDocumentStatus(ed.status || 'pdf_processing')
                      : ed.ready
                        ? `${ed.chunk_count || 0} clauses${ed.table_count ? ` · ${ed.table_count} tables` : ''}`
                        : ed.status === 'failed'
                          ? 'Failed — upload again'
                          : 'Not ingested'}
                  </p>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Word — clauses
                  <input
                    type="file"
                    accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    className="mt-1 block w-full text-sm"
                    onChange={(e) => setWordFiles((prev) => ({ ...prev, [ed.codebook]: e.target.files?.[0] || null }))}
                  />
                </label>
                <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  PDF — tables (Modal)
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    className="mt-1 block w-full text-sm"
                    onChange={(e) => setPdfFiles((prev) => ({ ...prev, [ed.codebook]: e.target.files?.[0] || null }))}
                  />
                </label>
                <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 sm:col-span-2">
                  Clause numbering
                  <select
                    className="augusta-input mt-1 w-full text-sm"
                    value={family}
                    onChange={(e) => setParserFamily((prev) => ({ ...prev, [ed.codebook]: e.target.value }))}
                  >
                    {STANDARD_FAMILIES.map((opt) => (
                      <option key={opt.id} value={opt.id}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                Best: Word for clauses + PDF for Modal tables. Word only or PDF only also works.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy || processing}
                  onClick={() => void uploadPdf(ed.codebook, ed.label)}
                  className="rounded-full bg-[#0b1220] px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
                >
                  {processing ? 'Extracting…' : 'Upload file'}
                </button>
                <button
                  type="button"
                  disabled={busy || processing || !(ed.chunk_count || 0)}
                  onClick={() => void downloadCsv(ed.codebook, 'clauses')}
                  className="rounded-full border border-slate-300 px-4 py-2 text-xs font-semibold disabled:opacity-50"
                >
                  Download clauses
                </button>
                <button
                  type="button"
                  disabled={busy || processing || !(ed.table_count || 0)}
                  onClick={() => void downloadCsv(ed.codebook, 'tables')}
                  className="rounded-full border border-slate-300 px-4 py-2 text-xs font-semibold disabled:opacity-50"
                >
                  Download tables
                </button>
                <button
                  type="button"
                  disabled={busy || processing}
                  onClick={() => void handleDeleteEdition(ed.codebook, ed.label)}
                  className="rounded-full border border-red-300 px-4 py-2 text-xs font-semibold text-red-700 disabled:opacity-50"
                >
                  Delete
                </button>
              </div>
              <details className="mt-4">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Already have CSVs?
                </summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Clause CSV
                    <input
                      type="file"
                      accept=".csv"
                      className="mt-1 block w-full text-sm"
                      onChange={(e) => setClauseFiles((prev) => ({ ...prev, [ed.codebook]: e.target.files?.[0] || null }))}
                    />
                  </label>
                  <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Tables CSV
                    <input
                      type="file"
                      accept=".csv"
                      className="mt-1 block w-full text-sm"
                      onChange={(e) => setTableFiles((prev) => ({ ...prev, [ed.codebook]: e.target.files?.[0] || null }))}
                    />
                  </label>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void uploadFor(ed.codebook, 'clauses')}
                    className="rounded-full bg-[#0b1220] px-4 py-2 text-xs font-semibold text-white"
                  >
                    Upload clauses
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void uploadFor(ed.codebook, 'tables')}
                    className="rounded-full border border-slate-300 px-4 py-2 text-xs font-semibold"
                  >
                    Upload tables
                  </button>
                </div>
              </details>
            </div>
            );
          })
        )}
      </div>

      <form onSubmit={handleAddEdition} className="grid gap-3 rounded-2xl border border-dashed border-slate-300 p-4 sm:grid-cols-3">
        <p className="sm:col-span-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Add a year under this volume
        </p>
        <input
          className="augusta-input sm:col-span-2"
          value={newEdition.label}
          onChange={(e) => setNewEdition((p) => ({ ...p, label: e.target.value }))}
          placeholder="Edition display name (e.g. NCC 2018 Vol 1 — Class 2–9)"
          required
        />
        <input
          className="augusta-input"
          value={newEdition.edition_year}
          onChange={(e) => setNewEdition((p) => ({ ...p, edition_year: e.target.value }))}
          placeholder="Year (e.g. 2018)"
        />
        <input
          className="augusta-input sm:col-span-2"
          value={newEdition.codebook}
          onChange={(e) => setNewEdition((p) => ({ ...p, codebook: e.target.value }))}
          placeholder="Code name (used as clause id, e.g. NCC2018_VOL1)"
        />
        <button type="submit" disabled={busy} className="rounded-full bg-[#0b1220] px-4 py-2 text-sm font-semibold text-white">
          Add edition
        </button>
      </form>
    </div>
  );

  if (loading) {
    return (
      <div className="flex min-h-[320px] items-center justify-center">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900">
        <p className="font-semibold">Library catalog is not available yet.</p>
        <p className="mt-2">
          Run <code className="rounded bg-white px-1">supabase/combined_setup.sql</code> in the Supabase SQL Editor (section 10b), then refresh.
        </p>
        <p className="mt-2 text-amber-800">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="augusta-eyebrow mb-2">Admin</p>
        <h2 className={`${typography.sectionTitle} text-slate-950`}>NCC library</h2>
        <p className={`${typography.helper} mt-2 max-w-3xl`}>
          Rename or delete a volume if the name is wrong. Add another volume (2018, 2019, 2022, 2025), then upload a
          Word file if you have one (better than PDF).
        </p>
      </div>
      {message && <p className="text-sm font-medium text-emerald-700">{message}</p>}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <LibraryTree
            countries={tree.countries}
            types={tree.types}
            documents={tree.documents}
            editions={tree.editions}
            countryId={countryId}
            typeId={typeId}
            documentId={documentId}
            onCountryChange={onCountryChange}
            onSelectType={(id) => {
              setTypeId(id);
              setDocumentId(null);
            }}
            onSelectDocument={(id) => {
              setDocumentId(id);
              const doc = tree.documents.find((d) => d.id === id);
              if (doc) setTypeId(doc.document_type_id);
            }}
            showUnready
            showEmptyTypes
          />
        </div>
        <div className="lg:col-span-8 space-y-6">
          {renderAddVolumeForm()}
          {selectedDoc
            ? renderDocumentPane(selectedDoc)
            : typeId
              ? renderTypePane()
              : renderCountryPane()}
        </div>
      </div>
    </div>
  );
};

export default LibraryAdminPanel;
