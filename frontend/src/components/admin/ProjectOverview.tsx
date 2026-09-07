import React, { useEffect, useMemo, useState } from 'react';
import { supabase } from '../../lib/supabase';

interface OverviewUser {
  id: string;
  email?: string | null;
  full_name?: string | null;
  role?: string | null;
  created_at?: string | null;
  last_activity?: string | null;
  document_count: number;
  chunk_count: number;
  statuses: string[];
  subscription_type?: string | null;
}

interface OverviewStats {
  total_users: number;
  total_documents: number;
  total_chunks: number;
  active_users: number;
  ready_documents: number;
  pending_documents: number;
}

interface OverviewResponse {
  stats: OverviewStats;
  users: OverviewUser[];
}

const cardClass = 'bg-white p-6 rounded-lg shadow border';

const formatDate = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString();
};

const roleClass = (role?: string | null) => {
  switch (role) {
    case 'admin':
      return 'bg-red-100 text-red-800';
    case 'engineer':
      return 'bg-blue-100 text-blue-800';
    case 'inspector':
      return 'bg-green-100 text-green-800';
    default:
      return 'bg-gray-100 text-gray-700';
  }
};

const ProjectOverview: React.FC = () => {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const fetchProjectData = async () => {
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
      const response = await fetch(getApiUrl('api/v1/admin/overview'), {
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail?.detail || 'Failed to fetch project overview');
      }

      const payload: OverviewResponse = await response.json();
      setData(payload);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : 'Failed to fetch project data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchProjectData();
  }, []);

  const filteredUsers = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    if (!query) return data.users;

    return data.users.filter((user) => {
      const sources = [
        user.full_name ?? '',
        user.email ?? '',
        user.id,
        user.role ?? '',
        user.subscription_type ?? '',
      ];
      return sources.some((value) => value.toLowerCase().includes(query));
    });
  }, [data, search]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <div className="text-red-600 mb-4">
          <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.732 16.5c-.77.833.192 2.5 1.732 2.5z" />
          </svg>
        </div>
        <h3 className="text-lg font-medium text-gray-900 mb-2">Error Loading Project Data</h3>
        <p className="text-gray-600 mb-4">{error}</p>
        <button
          onClick={fetchProjectData}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900">System Overview</h2>
          <p className="mt-1 text-sm text-gray-600">
            Registered users, ingestion footprint, and activation metrics across the platform.
          </p>
        </div>
        <button
          onClick={fetchProjectData}
          className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
          disabled={refreshing}
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {data && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-6 gap-4">
            <div className={cardClass}>
              <p className="text-sm text-gray-500">Total Users</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{data.stats.total_users}</p>
            </div>
            <div className={cardClass}>
              <p className="text-sm text-gray-500">Total Documents</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{data.stats.total_documents}</p>
            </div>
            <div className={cardClass}>
              <p className="text-sm text-gray-500">Total Chunks</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{data.stats.total_chunks}</p>
            </div>
            <div className={cardClass}>
              <p className="text-sm text-gray-500">Active Users</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{data.stats.active_users}</p>
            </div>
            <div className={cardClass}>
              <p className="text-sm text-gray-500">Ready Documents</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{data.stats.ready_documents}</p>
            </div>
            <div className={cardClass}>
              <p className="text-sm text-gray-500">Pending Documents</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{data.stats.pending_documents}</p>
            </div>
          </div>

          <div className="bg-white shadow rounded-lg">
            <div className="px-6 py-4 border-b border-gray-200 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
              <div>
                <h3 className="text-lg font-medium text-gray-900">Users &amp; Document Activity</h3>
                <p className="text-sm text-gray-600">
                  Every registered user and their ingestion footprint.
                </p>
              </div>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name, email, or user ID"
                className="w-full md:w-72 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Role</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Subscription</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Documents</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Chunks</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Statuses</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Joined</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Activity</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {filteredUsers.map((user) => (
                    <tr key={user.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center">
                          <div className="flex-shrink-0 h-10 w-10">
                            <div className="h-10 w-10 rounded-full bg-gray-100 flex items-center justify-center text-sm font-medium text-gray-700">
                              {(user.full_name || user.email || user.id).charAt(0).toUpperCase()}
                            </div>
                          </div>
                          <div className="ml-4">
                            <div className="text-sm font-medium text-gray-900">{user.full_name || 'Unknown User'}</div>
                            <div className="text-xs text-gray-500">{user.email || '—'}</div>
                            <div className="text-xs text-gray-400 mt-1">{user.id}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${roleClass(user.role)}`}>
                          {user.role || 'user'}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          user.subscription_type 
                            ? user.subscription_type === 'No Subscription' 
                              ? 'bg-gray-100 text-gray-600'
                              : 'bg-blue-100 text-blue-800'
                            : 'bg-gray-100 text-gray-600'
                        }`}>
                          {user.subscription_type || 'No Subscription'}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        <span className="font-medium">{user.document_count}</span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        <span className="font-medium">{user.chunk_count}</span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-500">
                        {user.statuses.length ? user.statuses.join(', ') : '—'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-500">{formatDate(user.created_at)}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-500">{formatDate(user.last_activity)}</td>
                    </tr>
                  ))}
                  {filteredUsers.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-6 py-10 text-center text-sm text-gray-400">
                        No users match the current filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default ProjectOverview;
