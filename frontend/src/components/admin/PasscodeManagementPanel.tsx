/**
 * Admin: Create and list promo passcodes; list redemptions.
 */
import React, { useState, useEffect } from 'react';
import { supabase } from '../../lib/supabase';
import { apiUrl } from '../../lib/api';

interface PasscodeRow {
  id: string;
  code: string;
  duration_months: number;
  max_redemptions: number;
  redemptions_used: number;
  is_active: boolean;
  created_at: string;
  notes?: string | null;
}

interface RedemptionRow {
  id: string;
  passcode_id: string;
  user_id: string;
  redeemed_at: string;
  access_expires_at: string;
  code?: string;
  user_email?: string;
}

export const PasscodeManagementPanel: React.FC = () => {
  const [passcodes, setPasscodes] = useState<PasscodeRow[]>([]);
  const [redemptions, setRedemptions] = useState<RedemptionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [createLoading, setCreateLoading] = useState(false);
  const [unusedOnly, setUnusedOnly] = useState(false);
  const [createForm, setCreateForm] = useState({
    count: 20,
    duration_months: 6,
    notes: '',
    prefix: 'PILOT-',
  });
  const [createdCodes, setCreatedCodes] = useState<PasscodeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchPasscodes = async () => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) return;
      const res = await fetch(
        apiUrl(`/api/v1/subscriptions/admin/passcodes?unused_only=${unusedOnly}`),
        { headers: { Authorization: `Bearer ${session.access_token}` } }
      );
      if (!res.ok) throw new Error('Failed to load passcodes');
      setPasscodes(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load passcodes');
    }
  };

  const fetchRedemptions = async () => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) return;
      const res = await fetch(
        apiUrl('/api/v1/subscriptions/admin/passcodes/redemptions'),
        { headers: { Authorization: `Bearer ${session.access_token}` } }
      );
      if (!res.ok) throw new Error('Failed to load redemptions');
      setRedemptions(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load redemptions');
    }
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchPasscodes(), fetchRedemptions()]).finally(() => setLoading(false));
  }, [unusedOnly]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreatedCodes(null);
    setCreateLoading(true);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error('Not authenticated');
      const res = await fetch(apiUrl('/api/v1/subscriptions/admin/passcodes/create'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(createForm),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Create failed');
      setCreatedCodes(data.passcodes || []);
      fetchPasscodes();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed');
    } finally {
      setCreateLoading(false);
    }
  };

  if (loading && passcodes.length === 0 && redemptions.length === 0) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-gray-600">Loading passcodes...</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
      <header className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-widest text-blue-600">Admin</p>
        <h2 className="mt-2 text-3xl font-semibold text-gray-900">Passcode management</h2>
        <p className="mt-2 text-sm text-gray-600">
          Create promo passcodes for professional trial (3 or 6 months). Each code is single-use. List redemptions below.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      {/* Create passcodes */}
      <div className="mb-10 rounded-xl border border-gray-200 bg-gray-50/50 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Create passcodes</h3>
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-700">Count</span>
            <input
              type="number"
              min={1}
              max={100}
              value={createForm.count}
              onChange={(e) => setCreateForm((f) => ({ ...f, count: parseInt(e.target.value, 10) || 1 }))}
              className="rounded border border-gray-300 px-3 py-2 w-24"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-700">Duration (months)</span>
            <select
              value={createForm.duration_months}
              onChange={(e) => setCreateForm((f) => ({ ...f, duration_months: parseInt(e.target.value, 10) }))}
              className="rounded border border-gray-300 px-3 py-2 w-32"
            >
              <option value={3}>3</option>
              <option value={6}>6</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-700">Prefix</span>
            <input
              type="text"
              value={createForm.prefix}
              onChange={(e) => setCreateForm((f) => ({ ...f, prefix: e.target.value || 'PILOT-' }))}
              className="rounded border border-gray-300 px-3 py-2 w-28"
              placeholder="PILOT-"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-700">Notes (optional)</span>
            <input
              type="text"
              value={createForm.notes}
              onChange={(e) => setCreateForm((f) => ({ ...f, notes: e.target.value }))}
              className="rounded border border-gray-300 px-3 py-2 w-48"
              placeholder="e.g. Pilot batch March 2025"
            />
          </label>
          <button
            type="submit"
            disabled={createLoading}
            className="rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {createLoading ? 'Creating...' : 'Create'}
          </button>
        </form>
        {createdCodes && createdCodes.length > 0 && (
          <div className="mt-4 rounded border border-green-200 bg-green-50 p-4">
            <p className="text-sm font-medium text-green-800 mb-2">Created {createdCodes.length} passcode(s). Copy to give to users:</p>
            <pre className="text-xs overflow-auto max-h-32 bg-white p-2 rounded border border-green-200">
              {createdCodes.map((r) => r.code).join('\n')}
            </pre>
          </div>
        )}
      </div>

      {/* List passcodes */}
      <div className="mb-10">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-900">Passcodes</h3>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={unusedOnly}
              onChange={(e) => setUnusedOnly(e.target.checked)}
            />
            Unused only
          </label>
        </div>
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Code</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Duration</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Used</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Active</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Created</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Notes</th>
              </tr>
            </thead>
            <tbody>
              {passcodes.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-mono text-gray-900">{row.code}</td>
                  <td className="px-4 py-2 text-gray-700">{row.duration_months} mo</td>
                  <td className="px-4 py-2 text-gray-700">{row.redemptions_used}/{row.max_redemptions}</td>
                  <td className="px-4 py-2">{row.is_active ? 'Yes' : 'No'}</td>
                  <td className="px-4 py-2 text-gray-600">{row.created_at ? new Date(row.created_at).toLocaleString() : '-'}</td>
                  <td className="px-4 py-2 text-gray-600">{row.notes || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {passcodes.length === 0 && <p className="text-gray-500 text-sm mt-2">No passcodes found.</p>}
      </div>

      {/* Redemptions */}
      <div>
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Redemptions</h3>
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Code</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">User email</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Redeemed</th>
                <th className="px-4 py-2 text-left font-medium text-gray-700">Access expires</th>
              </tr>
            </thead>
            <tbody>
              {redemptions.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-mono text-gray-900">{row.code || '-'}</td>
                  <td className="px-4 py-2 text-gray-700">{row.user_email || row.user_id}</td>
                  <td className="px-4 py-2 text-gray-600">{row.redeemed_at ? new Date(row.redeemed_at).toLocaleString() : '-'}</td>
                  <td className="px-4 py-2 text-gray-600">{row.access_expires_at ? new Date(row.access_expires_at).toLocaleDateString() : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {redemptions.length === 0 && <p className="text-gray-500 text-sm mt-2">No redemptions yet.</p>}
      </div>
    </section>
  );
};

export default PasscodeManagementPanel;
