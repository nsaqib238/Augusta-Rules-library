import React, { useEffect, useMemo, useState } from 'react';
import { supabase } from '../../lib/supabase';

interface RegistrySummary {
  total_documents: number;
  unique_users: number;
  ready_documents: number;
  pending_documents: number;
  duplicate_groups: number;
  total_duplicates: number;
}

interface RegistryOwner {
  id?: string;
  email?: string;
  full_name?: string;
  role?: string;
}

interface RegistryQueue {
  status?: string;
  priority?: number;
  admin_notes?: string;
  assigned_admin_id?: string;
  created_at?: string;
  updated_at?: string;
}

interface RegistryVersion {
  document_id: string;
  created_at?: string;
  status?: string;
  admin_status?: string;
  is_current: boolean;
}

interface RegistryEntry {
  id: string;
  user_id?: string;
  filename?: string;
  codebook?: string;
  discipline?: string;
  status?: string;
  admin_status?: string;
  chunk_count?: number;
  refined_chunk_count?: number;
  file_size?: number;
  storage_url?: string;
  processing_model?: string;
  created_at?: string;
  updated_at?: string;
  admin_processed_at?: string;
  owner?: RegistryOwner;
  queue?: RegistryQueue | null;
  duplicate_key?: string;
  duplicate_count: number;
  versions: RegistryVersion[];
}

interface RegistryResponse {
  summary: RegistrySummary;
  documents: RegistryEntry[];
}

const formatBytes = (value?: number) => {
  if (!value) return '—';
  if (value === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.floor(Math.log(value) / Math.log(1024));
  return `${(value / Math.pow(1024, index)).toFixed(1)} ${units[index]}`;
};

const formatDate = (value?: string) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString();
};

const statusColor = (status?: string) => {
  switch (status) {
    case 'ready_for_search':
      return 'bg-green-100 text-green-800';
    case 'pending_admin_review':
    case 'pending':
      return 'bg-yellow-100 text-yellow-800';
    case 'pdf_processing':
    case 'admin_processing':
    case 'processing':
      return 'bg-blue-100 text-blue-800';
    case 'failed':
    case 'rejected':
      return 'bg-red-100 text-red-800';
    default:
      return 'bg-gray-100 text-gray-700';
  }
};

const SummaryCard: React.FC<{ label: string; value: number | string; helper?: string; tone?: 'default' | 'accent' }> = ({
  label,
  value,
  helper,
  tone = 'default',
}) => {
  const toneClass =
    tone === 'accent' ? 'bg-blue-50 border-blue-100 text-blue-900' : 'bg-white border-gray-100 text-gray-900';
  return (
    <div className={`border rounded-lg p-4 shadow-sm ${toneClass}`}>
      <p className="text-sm text-gray-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{value}</p>
      {helper && <p className="mt-1 text-xs text-gray-400">{helper}</p>}
    </div>
  );
};

const DocumentRegistryPanel: React.FC = () => {
  const [data, setData] = useState<RegistryResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [duplicatesOnly, setDuplicatesOnly] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const fetchRegistry = async () => {
    try {
      setError(null);
      setRefreshing(true);
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error('Authentication required. Please sign in again.');
      }

      const { getApiUrl } = await import('../../lib/api');
      const response = await fetch(getApiUrl('api/v1/admin/document-registry?limit=200'), {
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail?.detail || 'Failed to fetch document registry');
      }

      const payload: RegistryResponse = await response.json();
      setData(payload);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : 'Failed to load document registry');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchRegistry();
  }, []);

  const filteredDocuments = useMemo(() => {
    if (!data) return [];

    return data.documents.filter((doc) => {
      const matchesSearch =
        !search ||
        doc.filename?.toLowerCase().includes(search.toLowerCase()) ||
        doc.owner?.email?.toLowerCase().includes(search.toLowerCase()) ||
        doc.owner?.full_name?.toLowerCase().includes(search.toLowerCase()) ||
        doc.codebook?.toLowerCase().includes(search.toLowerCase());

      const matchesStatus =
        statusFilter === 'all' ||
        doc.status === statusFilter ||
        doc.admin_status === statusFilter ||
        doc.queue?.status === statusFilter;

      const matchesDuplicates = duplicatesOnly ? doc.duplicate_count > 1 : true;

      return matchesSearch && matchesStatus && matchesDuplicates;
    });
  }, [data, search, statusFilter, duplicatesOnly]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="text-gray-500 text-sm">Loading document registry…</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900">Document Registry</h2>
          <p className="mt-1 text-sm text-gray-600">
            Canonical view of every document in the system. Track ownership, status, versions, duplicates, and chunk counts.
          </p>
        </div>
        <button
          onClick={fetchRegistry}
          className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
          disabled={refreshing}
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="border border-red-200 bg-red-50 text-red-700 px-4 py-3 rounded-md text-sm">{error}</div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <SummaryCard label="Total Documents" value={data.summary.total_documents} helper="Fetched in this view" />
            <SummaryCard label="Unique Users" value={data.summary.unique_users} />
            <SummaryCard
              label="Ready for Search"
              value={data.summary.ready_documents}
              helper={`${data.summary.pending_documents} waiting for admin`}
              tone="accent"
            />
            <SummaryCard
              label="Duplicate Flags"
              value={data.summary.duplicate_groups}
              helper={`${data.summary.total_duplicates} related copies`}
            />
          </div>

          <div className="bg-white border border-gray-100 rounded-lg shadow-sm p-4 space-y-4">
            <div className="flex flex-col lg:flex-row lg:items-center gap-3">
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by filename, owner, or codebook…"
                className="w-full lg:max-w-sm px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full lg:w-48 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="all">All statuses</option>
                <option value="ready_for_search">Ready for search</option>
                <option value="pending_admin_review">Pending admin review</option>
                <option value="pdf_processing">Processing PDF</option>
                <option value="admin_processing">Admin processing</option>
                <option value="pending">Queue: pending</option>
              </select>
              <label className="inline-flex items-center space-x-2 text-sm text-gray-600">
                <input
                  type="checkbox"
                  checked={duplicatesOnly}
                  onChange={(e) => setDuplicatesOnly(e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span>Show duplicates only</span>
              </label>
            </div>

            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    <th className="px-4 py-3">Document</th>
                    <th className="px-4 py-3">Owner</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Chunks</th>
                    <th className="px-4 py-3">Queue</th>
                    <th className="px-4 py-3">Versions</th>
                    <th className="px-4 py-3">Created</th>
                    <th className="px-4 py-3">Updated</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-100">
                  {filteredDocuments.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-4 py-10 text-center text-gray-400">
                        No documents match the current filters.
                      </td>
                    </tr>
                  )}
                  {filteredDocuments.map((doc) => (
                    <tr key={doc.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3 align-top">
                        <div className="font-medium text-gray-900">{doc.filename || 'Untitled document'}</div>
                        <div className="text-xs text-gray-500 flex items-center gap-2 mt-1">
                          <span>{doc.codebook || '—'}</span>
                          <span>•</span>
                          <span>{doc.discipline || '—'}</span>
                        </div>
                        <div className="text-xs text-gray-400 mt-1">{formatBytes(doc.file_size)}</div>
                        {doc.duplicate_count > 1 && (
                          <span className="inline-flex items-center mt-2 px-2 py-0.5 text-xs font-semibold rounded-full bg-amber-100 text-amber-800">
                            Duplicate group ({doc.duplicate_count})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <div className="text-sm text-gray-900">{doc.owner?.full_name || 'Unknown user'}</div>
                        <div className="text-xs text-gray-500">{doc.owner?.email || '—'}</div>
                        <div className="text-xs text-gray-400 mt-1">{doc.owner?.role || 'user'}</div>
                      </td>
                      <td className="px-4 py-3 align-top space-y-2">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${statusColor(doc.status)}`}>
                          {doc.status || '—'}
                        </span>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${statusColor(doc.admin_status)}`}>
                          Admin: {doc.admin_status || '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 align-top">
                        <div className="text-sm text-gray-900">{doc.chunk_count ?? 0}</div>
                        <div className="text-xs text-gray-500">
                          Refined: {doc.refined_chunk_count ?? 0}
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top">
                        {doc.queue ? (
                          <div className="space-y-1 text-xs text-gray-600">
                            <span className={`inline-flex px-2 py-0.5 rounded-full font-medium ${statusColor(doc.queue.status)}`}>
                              {doc.queue.status}
                            </span>
                            <div>Priority: {doc.queue.priority ?? 1}</div>
                            {doc.queue.admin_notes && (
                              <div className="text-gray-400 truncate max-w-xs">{doc.queue.admin_notes}</div>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-gray-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <div className="flex flex-col gap-1">
                          {doc.versions.slice(0, 3).map((version) => (
                            <span
                              key={version.document_id}
                              className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${
                                version.is_current ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-600'
                              }`}
                            >
                              {version.is_current ? 'Current' : 'Version'} • {formatDate(version.created_at)}
                            </span>
                          ))}
                          {doc.versions.length > 3 && (
                            <span className="text-xs text-gray-400">+ {doc.versions.length - 3} more</span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-gray-600">{formatDate(doc.created_at)}</td>
                      <td className="px-4 py-3 align-top text-xs text-gray-600">{formatDate(doc.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default DocumentRegistryPanel;