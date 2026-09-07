import React, { useState, useEffect, useMemo } from 'react';
import { apiRequest } from '../../lib/api';

interface AdminUserOption {
  id: string;
  email?: string;
  full_name?: string;
  role?: string;
  created_at?: string;
}

interface AdminCompanyMembership {
  id: string;
  company_id?: string;
  employee_role?: string;
  status?: string;
  invitation_accepted_at?: string;
  company?: {
    id?: string;
    name?: string;
    subscription_tier?: string;
    subscription_status?: string;
    max_employees?: number;
    current_employee_count?: number;
    created_at?: string;
  };
}

interface AdminUserDetail {
  profile?: Record<string, any>;
  memberships: AdminCompanyMembership[];
  company_employees: Record<string, Array<Record<string, any>>>;
  company_invitations: Record<string, Array<Record<string, any>>>;
  documents: Array<Record<string, any>>;
  subscriptions: Array<Record<string, any>>;
  admin_queue: Array<Record<string, any>>;
  invitations: Array<Record<string, any>>;
  stats: Record<string, number>;
}

const formatDate = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

const DatabaseInspector: React.FC = () => {
  const [userOptions, setUserOptions] = useState<AdminUserOption[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedUserId, setSelectedUserId] = useState('');
  const [userDetail, setUserDetail] = useState<AdminUserDetail | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchUserOptions();
  }, []);

  const filteredOptions = useMemo(() => {
    if (!searchTerm.trim()) return userOptions;
    const term = searchTerm.toLowerCase();
    return userOptions.filter(
      (user) =>
        user.email?.toLowerCase().includes(term) ||
        user.full_name?.toLowerCase().includes(term) ||
        user.id.toLowerCase().includes(term)
    );
  }, [searchTerm, userOptions]);

  const fetchUserOptions = async () => {
    try {
      setLoadingOptions(true);
      const response = await apiRequest('/api/v1/admin/users/options');
      if (!response.ok) {
        throw new Error('Failed to load user list');
      }
      const data = await response.json();
      setUserOptions(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load user list');
    } finally {
      setLoadingOptions(false);
    }
  };

  const fetchUserDetail = async (userId: string) => {
    if (!userId) {
      setUserDetail(null);
      return;
    }
    try {
      setLoadingDetail(true);
      setError(null);
      const response = await apiRequest(`/api/v1/admin/users/${userId}/database`);
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Failed to load user details' }));
        throw new Error(errorData.detail || 'Failed to load user details');
      }
      const data = await response.json();
      setUserDetail(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load user details');
      setUserDetail(null);
    } finally {
      setLoadingDetail(false);
    }
  };

  const handleSelectUser = (userId: string) => {
    setSelectedUserId(userId);
    fetchUserDetail(userId);
  };

  const renderProfileSection = () => {
    if (!userDetail?.profile) return null;

    // Clone profile so we can safely add derived / human-friendly fields
    const profile = { ...userDetail.profile };

    // Normalise subscription type for display
    // We want to show tiers like "Sole Trader", "Individual", "Professional"
    // instead of raw Stripe price IDs or internal codes.
    const PLAN_DISPLAY_NAMES: Record<string, string> = {
      sole_trader_free: 'Sole Trader',
      individual_monthly: 'Individual',
      professional: 'Professional',
    };

    // Helper to convert internal plan name to a nice label
    const getPlanLabel = (planName?: string | null): string | undefined => {
      if (!planName) return undefined;
      if (PLAN_DISPLAY_NAMES[planName]) return PLAN_DISPLAY_NAMES[planName];
      // Fallback: convert snake_case to Title Case
      return planName
        .split('_')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
    };

    // Prefer subscription data if present
    const primarySubscription = userDetail.subscriptions?.[0];
    if (primarySubscription) {
      const planName =
        (primarySubscription.plan_name as string | undefined) ||
        (primarySubscription.plan_id as string | undefined) ||
        (profile.subscription_type as string | undefined);

      const label = getPlanLabel(planName);
      if (label) {
        profile.subscription_type = label;
      }
    } else {
      // No subscriptions in DB → treat as free / Sole Trader tier
      const existing = profile.subscription_type as string | undefined;
      const label =
        getPlanLabel(existing) ||
        (existing && existing.startsWith('price_') ? 'Individual' : undefined) ||
        'Sole Trader';
      profile.subscription_type = label;
    }

    return (
      <div className="bg-white shadow overflow-hidden sm:rounded-lg">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-100">
          <h3 className="text-lg leading-6 font-medium text-gray-900">Profile</h3>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">User profile record in the database.</p>
        </div>
        <div className="px-4 py-5 sm:p-6">
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4">
            {Object.entries(profile).map(([key, value]) => (
              <div key={key}>
                <dt className="text-sm font-medium text-gray-500 uppercase tracking-wide">{key}</dt>
                <dd className="mt-1 text-sm text-gray-900 break-all">
                  {typeof value === 'string' && key.includes('at') ? formatDate(value) : String(value ?? '—')}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    );
  };

  const renderMemberships = () => {
    if (!userDetail?.memberships?.length) return null;
    return (
      <div className="bg-white shadow overflow-hidden sm:rounded-lg">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-100 flex items-center justify-between">
          <div>
            <h3 className="text-lg leading-6 font-medium text-gray-900">Company Memberships</h3>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">Roles within company accounts.</p>
          </div>
        </div>
        <div className="px-4 py-5 sm:p-6 space-y-6">
          {userDetail.memberships.map((membership) => (
            <div key={membership.id} className="border border-gray-200 rounded-lg p-4">
              <div className="flex flex-wrap justify-between items-center gap-4">
                <div>
                  <p className="text-sm text-gray-500">Company</p>
                  <p className="text-base font-semibold text-gray-900">
                    {membership.company?.name || membership.company_id || 'Unknown Company'}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm text-gray-500">Role</p>
                  <p className="text-base font-semibold capitalize text-gray-900">{membership.employee_role || 'member'}</p>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4 text-sm text-gray-600">
                <div>
                  <p className="text-gray-500">Status</p>
                  <p className="font-medium">{membership.status || '—'}</p>
                </div>
                <div>
                  <p className="text-gray-500">Accepted</p>
                  <p className="font-medium">{formatDate(membership.invitation_accepted_at)}</p>
                </div>
                {membership.company && (
                  <div>
                    <p className="text-gray-500">Tier</p>
                    <p className="font-medium">
                      {membership.company.subscription_tier} • {membership.company.subscription_status}
                    </p>
                    <p className="text-xs text-gray-500">
                      {membership.company.current_employee_count}/{membership.company.max_employees} employees
                    </p>
                  </div>
                )}
              </div>
              {membership.company_id && userDetail.company_employees?.[membership.company_id] && (
                <div className="mt-4">
                  <p className="text-sm font-semibold text-gray-800 mb-2">Employees</p>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 text-sm">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Name</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Email</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Role</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Status</th>
                        </tr>
                      </thead>
                      <tbody className="bg-white divide-y divide-gray-200">
                        {userDetail.company_employees[membership.company_id].map((employee) => (
                          <tr key={employee.id}>
                            <td className="px-3 py-2 whitespace-nowrap">{employee.profiles?.full_name || '—'}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{employee.profiles?.email || '—'}</td>
                            <td className="px-3 py-2 whitespace-nowrap capitalize">{employee.employee_role || 'member'}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{employee.status || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {membership.company_id && userDetail.company_invitations?.[membership.company_id]?.length > 0 && (
                <div className="mt-4">
                  <p className="text-sm font-semibold text-gray-800 mb-2">Pending Invitations</p>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 text-sm">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Email</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Role</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Status</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Invited</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider">Expires</th>
                        </tr>
                      </thead>
                      <tbody className="bg-white divide-y divide-gray-200">
                        {userDetail.company_invitations[membership.company_id].map((invite) => (
                          <tr key={invite.id}>
                            <td className="px-3 py-2 whitespace-nowrap">{invite.email}</td>
                            <td className="px-3 py-2 whitespace-nowrap capitalize">{invite.employee_role || 'member'}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{invite.status || 'pending'}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{formatDate(invite.created_at)}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{formatDate(invite.expires_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderTable = (title: string, rows: Array<Record<string, any>>, columns: string[]) => {
    if (!rows.length) return null;
    return (
      <div className="bg-white shadow overflow-hidden sm:rounded-lg">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-100">
          <h3 className="text-lg leading-6 font-medium text-gray-900">{title}</h3>
        </div>
        <div className="px-4 py-5 sm:p-6 overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                {columns.map((column) => (
                  <th
                    key={column}
                    className="px-3 py-2 text-left font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {rows.map((row) => (
                <tr key={row.id}>
                  {columns.map((column) => {
                    const value = row[column] ?? row[column.toLowerCase()] ?? row[column.replace(/\s+/g, '_').toLowerCase()];
                    return (
                      <td key={`${row.id}-${column}`} className="px-3 py-2 whitespace-nowrap">
                        {typeof value === 'string' && column.toLowerCase().includes('date')
                          ? formatDate(value)
                          : String(value ?? '—')}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const statsCards = userDetail
    ? [
        { label: 'Total Documents', value: userDetail.stats?.total_documents ?? 0 },
        { label: 'Ready Documents', value: userDetail.stats?.ready_documents ?? 0 },
        { label: 'Pending Documents', value: userDetail.stats?.pending_documents ?? 0 },
        { label: 'Company Memberships', value: userDetail.stats?.company_memberships ?? 0 },
        { label: 'Subscriptions', value: userDetail.stats?.subscriptions ?? 0 },
        { label: 'Admin Queue Items', value: userDetail.stats?.admin_queue_items ?? 0 }
      ]
    : [];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900">Database Inspector</h2>
        <p className="mt-1 text-sm text-gray-600">Inspect a user profile, documents, subscriptions, and admin queue entries.</p>
      </div>
      <div className="rounded-lg bg-white p-6 shadow">
        <div className="flex flex-col gap-4 md:flex-row md:items-end">
          <div className="flex-1">
            <label htmlFor="user-search" className="block text-sm font-medium text-gray-700">
              Search Users
            </label>
            <input
              id="user-search"
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Filter by email, name, or user ID"
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
          <div className="flex-1">
            <label htmlFor="user-select" className="block text-sm font-medium text-gray-700">
              Select User
            </label>
            <select
              id="user-select"
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
              value={selectedUserId}
              onChange={(e) => handleSelectUser(e.target.value)}
              disabled={loadingOptions}
            >
              <option value="">Select a user</option>
              {filteredOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.full_name || option.email || option.id} ({option.email || 'no email'})
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end gap-2">
            <button
              onClick={fetchUserOptions}
              className="inline-flex items-center px-4 py-2 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50"
            >
              Refresh List
            </button>
            {selectedUserId && (
              <button
                onClick={() => fetchUserDetail(selectedUserId)}
                className="inline-flex items-center px-4 py-2 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700"
              >
                Reload User
              </button>
            )}
          </div>
        </div>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </div>

      {loadingDetail && <p className="text-sm text-gray-600">Loading user data…</p>}

      {statsCards.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-4">
          {statsCards.map((card) => (
            <div key={card.label} className="bg-white border border-gray-200 rounded-lg p-4 text-center">
              <p className="text-sm text-gray-500">{card.label}</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{card.value}</p>
            </div>
          ))}
        </div>
      )}

      {renderProfileSection()}
      {renderMemberships()}
      {renderTable('Subscriptions', userDetail?.subscriptions || [], [
        'plan_name',
        'subscription_type',
        'status',
        'current_period_start',
        'current_period_end'
      ])}
      {renderTable('Documents', userDetail?.documents || [], [
        'filename',
        'codebook',
        'discipline',
        'status',
        'admin_status',
        'created_at'
      ])}
      {renderTable('Admin Queue Entries', userDetail?.admin_queue || [], ['filename', 'codebook', 'status', 'priority', 'created_at'])}
      {renderTable('Invitations (by email)', userDetail?.invitations || [], ['company_id', 'employee_role', 'status', 'expires_at', 'created_at'])}
    </div>
  );
};

export default DatabaseInspector;

