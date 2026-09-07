/**
 * Admin: referral partners + company list (attribution / commission tracking).
 */
import React, { useEffect, useState } from 'react';
import { supabase } from '../../lib/supabase';
import { apiUrl } from '../../lib/api';

interface Partner {
  id: string;
  name: string;
  code: string;
  is_active: boolean;
  notes?: string | null;
  commission_note?: string | null;
  login_email?: string | null;
  created_at: string;
}

interface CompanyRow {
  id: string;
  name: string;
  status: string;
  max_seats: number;
  seats_used: number;
  join_code: string;
  owner_user_id: string;
  owner_email?: string | null;
  referred_by_partner_id?: string | null;
  partner_name?: string | null;
  partner_code?: string | null;
  stripe_subscription_id?: string | null;
  created_at: string;
}

export const CompanyReferralAdminPanel: React.FC = () => {
  const [partners, setPartners] = useState<Partner[]>([]);
  const [companies, setCompanies] = useState<CompanyRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createLoading, setCreateLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'pending' | 'inactive'>('all');
  const [partnerFilter, setPartnerFilter] = useState<'all' | 'with_partner' | 'no_partner' | string>('all');
  const [search, setSearch] = useState('');
  const [form, setForm] = useState({
    name: '',
    code: '',
    notes: '',
    commission_note: '',
    login_email: '',
  });

  const authHeaders = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) throw new Error('Not authenticated');
    return { Authorization: `Bearer ${session.access_token}` };
  };

  const load = async () => {
    setError(null);
    const headers = await authHeaders();
    const [pRes, cRes] = await Promise.all([
      fetch(apiUrl('/api/v1/subscriptions/admin/referral-partners'), { headers }),
      fetch(apiUrl('/api/v1/subscriptions/admin/companies'), { headers }),
    ]);
    if (!pRes.ok) throw new Error('Failed to load referral partners');
    if (!cRes.ok) throw new Error('Failed to load companies');
    setPartners(await pRes.json());
    setCompanies(await cRes.json());
  };

  useEffect(() => {
    setLoading(true);
    load()
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateLoading(true);
    setError(null);
    try {
      const headers = {
        ...(await authHeaders()),
        'Content-Type': 'application/json',
      };
      const res = await fetch(apiUrl('/api/v1/subscriptions/admin/referral-partners'), {
        method: 'POST',
        headers,
        body: JSON.stringify({
          name: form.name.trim(),
          code: form.code.trim().toUpperCase(),
          notes: form.notes.trim() || null,
          commission_note: form.commission_note.trim() || null,
          login_email: form.login_email.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Create failed');
      setForm({ name: '', code: '', notes: '', commission_note: '', login_email: '' });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed');
    } finally {
      setCreateLoading(false);
    }
  };

  const filteredCompanies = companies.filter((c) => {
    if (statusFilter !== 'all' && c.status !== statusFilter) return false;

    const hasPartner = !!(c.partner_code || c.partner_name || c.referred_by_partner_id);
    if (partnerFilter === 'with_partner' && !hasPartner) return false;
    if (partnerFilter === 'no_partner' && hasPartner) return false;
    if (
      partnerFilter !== 'all' &&
      partnerFilter !== 'with_partner' &&
      partnerFilter !== 'no_partner' &&
      (c.partner_code || '').toUpperCase() !== partnerFilter.toUpperCase() &&
      c.referred_by_partner_id !== partnerFilter
    ) {
      return false;
    }

    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      (c.name || '').toLowerCase().includes(q) ||
      (c.owner_email || '').toLowerCase().includes(q) ||
      (c.join_code || '').toLowerCase().includes(q) ||
      (c.partner_code || '').toLowerCase().includes(q) ||
      (c.partner_name || '').toLowerCase().includes(q)
    );
  });

  if (loading) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-gray-600">Loading companies & referrals…</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm space-y-10">
      <header>
        <p className="text-xs font-semibold uppercase tracking-widest text-blue-600">Admin</p>
        <h2 className="mt-2 text-3xl font-semibold text-gray-900">Companies & referrals</h2>
        <p className="mt-2 text-sm text-gray-600">
          Create marketing referral codes for partners. Link each partner to their Augusta login email so they get a Partner tab. Track attributed companies. Commission is paid outside the app while company status is active.
        </p>
      </header>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div>
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Create referral partner</h3>
        <form onSubmit={handleCreate} className="grid gap-3 sm:grid-cols-2">
          <input
            className="augusta-input"
            placeholder="Partner name"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            required
          />
          <input
            className="augusta-input"
            placeholder="Referral code (e.g. ACME-REF)"
            value={form.code}
            onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
            required
          />
          <input
            className="augusta-input"
            type="email"
            placeholder="Partner login email (their Augusta account)"
            value={form.login_email}
            onChange={(e) => setForm((f) => ({ ...f, login_email: e.target.value }))}
          />
          <input
            className="augusta-input"
            placeholder="Notes (optional)"
            value={form.notes}
            onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
          />
          <input
            className="augusta-input"
            placeholder="Commission note (optional)"
            value={form.commission_note}
            onChange={(e) => setForm((f) => ({ ...f, commission_note: e.target.value }))}
          />
          <button
            type="submit"
            disabled={createLoading}
            className="augusta-button-primary sm:col-span-2 justify-center"
          >
            {createLoading ? 'Creating…' : 'Create partner'}
          </button>
        </form>
      </div>

      <div>
        <h3 className="text-lg font-semibold text-gray-900 mb-3">Referral partners</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Code</th>
                <th className="py-2 pr-4">Login email</th>
                <th className="py-2 pr-4">Active</th>
                <th className="py-2 pr-4">Commission note</th>
              </tr>
            </thead>
            <tbody>
              {partners.map((p) => (
                <tr key={p.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4 font-medium text-gray-900">{p.name}</td>
                  <td className="py-2 pr-4 font-mono">{p.code}</td>
                  <td className="py-2 pr-4 text-gray-600">{p.login_email || '—'}</td>
                  <td className="py-2 pr-4">{p.is_active ? 'Yes' : 'No'}</td>
                  <td className="py-2 pr-4 text-gray-600">{p.commission_note || '—'}</td>
                </tr>
              ))}
              {partners.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-4 text-gray-500">No partners yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Companies</h3>
            <p className="mt-1 text-xs text-gray-500">
              Showing {filteredCompanies.length} of {companies.length}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              className="augusta-input min-w-[180px]"
              placeholder="Search name, owner, code…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="augusta-input"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              aria-label="Filter by status"
            >
              <option value="all">All statuses</option>
              <option value="active">Active only</option>
              <option value="pending">Pending only</option>
              <option value="inactive">Inactive only</option>
            </select>
            <select
              className="augusta-input"
              value={partnerFilter}
              onChange={(e) => setPartnerFilter(e.target.value)}
              aria-label="Filter by partner"
            >
              <option value="all">All partners</option>
              <option value="with_partner">Has partner</option>
              <option value="no_partner">No partner</option>
              {partners.map((p) => (
                <option key={p.id} value={p.code}>
                  Partner: {p.code}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Owner</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Seats</th>
                <th className="py-2 pr-4">Join code</th>
                <th className="py-2 pr-4">Partner</th>
              </tr>
            </thead>
            <tbody>
              {filteredCompanies.map((c) => (
                <tr key={c.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4 font-medium text-gray-900">{c.name}</td>
                  <td className="py-2 pr-4 text-gray-600">{c.owner_email || '—'}</td>
                  <td className="py-2 pr-4">{c.status}</td>
                  <td className="py-2 pr-4">
                    {c.seats_used}/{c.max_seats}
                  </td>
                  <td className="py-2 pr-4 font-mono">{c.join_code}</td>
                  <td className="py-2 pr-4 text-gray-600">
                    {c.partner_code || c.partner_name || '—'}
                  </td>
                </tr>
              ))}
              {filteredCompanies.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-4 text-gray-500">
                    {companies.length === 0 ? 'No companies yet.' : 'No companies match these filters.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};

export default CompanyReferralAdminPanel;
