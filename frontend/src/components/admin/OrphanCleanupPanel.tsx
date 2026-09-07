import React, { useCallback, useMemo, useState } from 'react';
import { apiRequest } from '../../lib/api';

type OrphanStorageObject = {
  bucket: string;
  path: string;
  name?: string;
  size?: number | null;
  updated_at?: string | null;
  referenced?: boolean | null;
  note?: string | null;
};

type OrphanChunkGroup = {
  document_id: string;
  chunks_count: number;
};

type OrphansSummary = {
  storage_orphans: OrphanStorageObject[];
  benchmark_orphans: OrphanStorageObject[];
  chunk_orphans: OrphanChunkGroup[];
  notes?: string[];
};

export const OrphanCleanupPanel: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<OrphansSummary | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiRequest('/api/v1/admin/orphans/summary', { method: 'GET' });
      const body = (await resp.json().catch(() => ({}))) as OrphansSummary & { detail?: string };
      if (!resp.ok) {
        throw new Error(body.detail || 'Failed to load orphans summary');
      }
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteStorageObject = useCallback(
    async (obj: OrphanStorageObject) => {
      const key = `storage:${obj.bucket}:${obj.path}`;
      if (!window.confirm(`Delete storage object?\n\n${obj.bucket}/${obj.path}`)) return;
      setBusyKey(key);
      setError(null);
      try {
        const resp = await apiRequest('/api/v1/admin/orphans/storage/delete', {
          method: 'POST',
          body: JSON.stringify({ bucket: obj.bucket, path: obj.path }),
        });
        const body = (await resp.json().catch(() => ({}))) as { ok?: boolean; detail?: string };
        if (!resp.ok || !body.ok) {
          throw new Error(body.detail || 'Storage delete failed');
        }
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Delete failed');
      } finally {
        setBusyKey(null);
      }
    },
    [refresh],
  );

  const deleteChunksForDocument = useCallback(
    async (documentId: string) => {
      const key = `chunks:${documentId}`;
      if (!window.confirm(`Delete ALL chunks for orphan document_id?\n\n${documentId}`)) return;
      setBusyKey(key);
      setError(null);
      try {
        const resp = await apiRequest('/api/v1/admin/orphans/chunks/delete', {
          method: 'POST',
          body: JSON.stringify({ document_id: documentId }),
        });
        const body = (await resp.json().catch(() => ({}))) as { ok?: boolean; detail?: string };
        if (!resp.ok || !body.ok) {
          throw new Error(body.detail || 'Chunk delete failed');
        }
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Delete failed');
      } finally {
        setBusyKey(null);
      }
    },
    [refresh],
  );

  const notes = useMemo(() => data?.notes || [], [data?.notes]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">🧹 Orphan Cleanup</h2>
          <p className="text-sm text-gray-600">
            Finds leftover Storage objects and DB chunks that no longer have a corresponding document record.
          </p>
        </div>
        <button
          onClick={() => void refresh()}
          className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-60"
          disabled={loading}
        >
          {loading ? 'Scanning…' : 'Scan for orphans'}
        </button>
      </div>

      {error && <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {notes.length > 0 && (
        <div className="rounded border border-gray-200 bg-white p-3 text-sm text-gray-700">
          <div className="font-semibold">Notes</div>
          <ul className="list-disc pl-5">
            {notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="rounded border border-gray-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold">Orphan PDFs / uploads (Storage)</h3>
          <span className="text-sm text-gray-500">{data?.storage_orphans?.length ?? 0} items</span>
        </div>
        <div className="overflow-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-700">Object</th>
                <th className="px-3 py-2 text-left font-medium text-gray-700">Referenced</th>
                <th className="px-3 py-2 text-left font-medium text-gray-700">Updated</th>
                <th className="px-3 py-2 text-right font-medium text-gray-700">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(data?.storage_orphans || []).map((o) => {
                const k = `storage:${o.bucket}:${o.path}`;
                const ref =
                  o.referenced === true ? 'yes' : o.referenced === false ? 'no' : o.referenced === null ? 'unknown' : '';
                return (
                  <tr key={k}>
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs">{o.bucket}/{o.path}</div>
                      {o.note && <div className="text-xs text-gray-500">{o.note}</div>}
                    </td>
                    <td className="px-3 py-2">{ref}</td>
                    <td className="px-3 py-2">{o.updated_at || ''}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => void deleteStorageObject(o)}
                        className="rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700 disabled:opacity-60"
                        disabled={busyKey === k}
                      >
                        {busyKey === k ? 'Deleting…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!data?.storage_orphans?.length && (
                <tr>
                  <td className="px-3 py-3 text-gray-500" colSpan={4}>
                    No items (run scan).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded border border-gray-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold">Orphan benchmark JSON (Storage)</h3>
          <span className="text-sm text-gray-500">{data?.benchmark_orphans?.length ?? 0} items</span>
        </div>
        <div className="overflow-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-700">Object</th>
                <th className="px-3 py-2 text-left font-medium text-gray-700">Updated</th>
                <th className="px-3 py-2 text-right font-medium text-gray-700">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(data?.benchmark_orphans || []).map((o) => {
                const k = `bench:${o.bucket}:${o.path}`;
                return (
                  <tr key={k}>
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs">{o.bucket}/{o.path}</div>
                      {o.note && <div className="text-xs text-gray-500">{o.note}</div>}
                    </td>
                    <td className="px-3 py-2">{o.updated_at || ''}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => void deleteStorageObject(o)}
                        className="rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700 disabled:opacity-60"
                        disabled={busyKey === `storage:${o.bucket}:${o.path}`}
                      >
                        {busyKey === `storage:${o.bucket}:${o.path}` ? 'Deleting…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!data?.benchmark_orphans?.length && (
                <tr>
                  <td className="px-3 py-3 text-gray-500" colSpan={3}>
                    No items (run scan).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded border border-gray-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold">Orphan chunks (DB)</h3>
          <span className="text-sm text-gray-500">{data?.chunk_orphans?.length ?? 0} document IDs</span>
        </div>
        <div className="overflow-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-700">document_id</th>
                <th className="px-3 py-2 text-left font-medium text-gray-700">chunks_count</th>
                <th className="px-3 py-2 text-right font-medium text-gray-700">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(data?.chunk_orphans || []).map((c) => {
                const k = `chunks:${c.document_id}`;
                return (
                  <tr key={k}>
                    <td className="px-3 py-2 font-mono text-xs">{c.document_id}</td>
                    <td className="px-3 py-2">{c.chunks_count}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => void deleteChunksForDocument(c.document_id)}
                        className="rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700 disabled:opacity-60"
                        disabled={busyKey === k}
                      >
                        {busyKey === k ? 'Deleting…' : 'Delete chunks'}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!data?.chunk_orphans?.length && (
                <tr>
                  <td className="px-3 py-3 text-gray-500" colSpan={3}>
                    No items (run scan).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

