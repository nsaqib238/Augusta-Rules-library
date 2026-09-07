import React, { useState, useEffect } from 'react';
import { getApiUrl } from '../../lib/api';
import { supabase } from '../../lib/supabase';

interface AdminStats {
  total_users: number;
  total_documents: number;
  pending_documents: number;
  completed_documents: number;
  total_chunks: number;
  processing_time_avg?: number;
  queue_wait_time?: number;
}

interface UserActivity {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  document_count: number;
  total_chunks: number;
  last_activity: string;
  created_at: string;
}

interface DocumentStats {
  codebook: string;
  count: number;
  avg_chunks: number;
  avg_processing_time: number;
}

const SystemAnalytics: React.FC = () => {
  const [adminStats, setAdminStats] = useState<AdminStats | null>(null);
  const [userActivity, setUserActivity] = useState<UserActivity[]>([]);
  const [documentStats, setDocumentStats] = useState<DocumentStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<'7d' | '30d' | '90d' | 'all'>('30d');

  useEffect(() => {
    void fetchAnalyticsData();
  }, [timeRange]);

  const fetchAnalyticsData = async () => {
    try {
      setLoading(true);
      await Promise.all([fetchAdminStats(), fetchUserActivity(), fetchDocumentStats()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch analytics data');
    } finally {
      setLoading(false);
    }
  };

  const fetchAdminStats = async () => {
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) throw new Error('No active session');

      const response = await fetch(getApiUrl('api/v1/admin/stats'), {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to fetch admin stats');
      }

      setAdminStats(await response.json());
    } catch (err) {
      console.error('Error fetching admin stats:', err);
    }
  };

  const fetchUserActivity = async () => {
    try {
      const { data: allProfiles, error: allProfilesError } = await supabase
        .from('profiles')
        .select('*')
        .order('created_at', { ascending: false });

      if (allProfilesError) {
        throw new Error(`Failed to fetch profiles: ${allProfilesError.message}`);
      }

      const { data: documents, error: docsError } = await supabase
        .from('documents')
        .select('user_id, created_at')
        .order('created_at', { ascending: false });

      if (docsError) {
        throw new Error(`Failed to fetch documents: ${docsError.message}`);
      }

      const uniqueUserIds = Array.from(new Set((documents || []).map((doc) => doc.user_id)));
      const userMap = new Map<string, Record<string, string>>();

      (allProfiles || []).forEach((user) => {
        userMap.set(user.id, user);
      });

      uniqueUserIds.forEach((userId) => {
        if (!userMap.has(userId)) {
          userMap.set(userId, {
            id: userId,
            email: `User ID: ${userId.slice(0, 8)}...`,
            full_name: `Document Uploader (${userId.slice(0, 8)})`,
            role: 'user',
            created_at: new Date().toISOString(),
          });
        }
      });

      const allUsers = Array.from(userMap.values());

      const userStatsData = await Promise.all(
        allUsers.map(async (user) => {
          const { data: userDocs } = await supabase.from('documents').select('id').eq('user_id', user.id);
          const documentCount = userDocs?.length || 0;

          let totalChunks = 0;
          if (documentCount > 0) {
            const { data: chunks } = await supabase.from('chunks').select('id').eq('user_id', user.id);
            totalChunks = chunks?.length || 0;
          }

          const { data: lastDoc } = await supabase
            .from('documents')
            .select('created_at')
            .eq('user_id', user.id)
            .order('created_at', { ascending: false })
            .limit(1);

          return {
            user_id: user.id,
            email: user.email,
            full_name: user.full_name,
            role: user.role,
            document_count: documentCount,
            total_chunks: totalChunks,
            last_activity: lastDoc?.[0]?.created_at || user.created_at,
            created_at: user.created_at,
          };
        })
      );

      setUserActivity(userStatsData);
    } catch (err) {
      console.error('Error fetching user activity:', err);
    }
  };

  const fetchDocumentStats = async () => {
    try {
      const { data: documents, error: docsError } = await supabase
        .from('documents')
        .select('codebook, chunk_count, created_at')
        .order('created_at', { ascending: false });

      if (docsError) {
        throw new Error(`Failed to fetch documents: ${docsError.message}`);
      }

      const codebookStats = (documents || []).reduce(
        (acc: Record<string, { codebook: string; count: number; total_chunks: number }>, doc) => {
          const codebook = doc.codebook || 'Unknown';
          if (!acc[codebook]) {
            acc[codebook] = { codebook, count: 0, total_chunks: 0 };
          }
          acc[codebook].count++;
          acc[codebook].total_chunks += doc.chunk_count || 0;
          return acc;
        },
        {}
      );

      setDocumentStats(
        Object.values(codebookStats).map((stat) => ({
          codebook: stat.codebook,
          count: stat.count,
          avg_chunks: stat.count > 0 ? Math.round(stat.total_chunks / stat.count) : 0,
          avg_processing_time: 0,
        }))
      );
    } catch (err) {
      console.error('Error fetching document stats:', err);
    }
  };

  const formatDate = (dateString: string) =>
    new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });

  const getRoleColor = (role: string) => {
    switch (role) {
      case 'admin':
        return 'bg-red-100 text-red-800';
      case 'engineer':
        return 'bg-blue-100 text-blue-800';
      case 'inspector':
        return 'bg-green-100 text-green-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="h-12 w-12 animate-spin rounded-full border-b-2 border-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-12 text-center">
        <h3 className="mb-2 text-lg font-medium text-gray-900">Error Loading Analytics</h3>
        <p className="mb-4 text-gray-600">{error}</p>
        <button onClick={() => void fetchAnalyticsData()} className="rounded-md bg-blue-600 px-4 py-2 text-white hover:bg-blue-700">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">System Analytics</h2>
          <p className="mt-1 text-gray-600">Performance metrics and usage statistics</p>
        </div>
        <div className="flex items-center space-x-4">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as typeof timeRange)}
            className="rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
            <option value="all">All time</option>
          </select>
          <button onClick={() => void fetchAnalyticsData()} className="rounded-md bg-blue-600 px-4 py-2 text-white hover:bg-blue-700">
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
        {[
          { label: 'Total Users', value: adminStats?.total_users || 0 },
          { label: 'Total Documents', value: adminStats?.total_documents || 0 },
          { label: 'Pending Documents', value: adminStats?.pending_documents || 0 },
          { label: 'Total Chunks', value: adminStats?.total_chunks || 0 },
        ].map((item) => (
          <div key={item.label} className="overflow-hidden rounded-lg bg-white shadow">
            <div className="p-5">
              <p className="truncate text-sm font-medium text-gray-500">{item.label}</p>
              <p className="mt-1 text-lg font-medium text-gray-900">{item.value}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-lg bg-white shadow">
        <div className="border-b border-gray-200 px-6 py-4">
          <h3 className="text-lg font-medium text-gray-900">Document Statistics by Codebook</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Codebook</th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Documents</th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Avg Chunks</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {documentStats.map((stat) => (
                <tr key={stat.codebook}>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-900">{stat.codebook}</td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-900">{stat.count}</td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-900">{stat.avg_chunks}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg bg-white shadow">
        <div className="border-b border-gray-200 px-6 py-4">
          <h3 className="text-lg font-medium text-gray-900">User Activity</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">User</th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Role</th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Documents</th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Chunks</th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Last Activity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {userActivity.slice(0, 10).map((user) => (
                <tr key={user.user_id}>
                  <td className="whitespace-nowrap px-6 py-4">
                    <div className="text-sm font-medium text-gray-900">{user.full_name}</div>
                    <div className="text-sm text-gray-500">{user.email}</div>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4">
                    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${getRoleColor(user.role)}`}>
                      {user.role}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-900">{user.document_count}</td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-900">{user.total_chunks}</td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">{formatDate(user.last_activity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default SystemAnalytics;
