/**
 * Marketing partner dashboard — companies attributed to this partner's referral code.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { apiUrl } from '../lib/api';

interface PartnerInfo {
  id: string;
  name: string;
  code: string;
  commission_note?: string | null;
  login_email?: string | null;
}

interface PartnerCompany {
  id: string;
  name: string;
  status: string;
  max_seats: number;
  seats_used: number;
  plan_label: string;
  owner_email?: string | null;
  owner_name?: string | null;
  created_at?: string;
}

interface PartnerPayload {
  is_partner: boolean;
  partner?: PartnerInfo;
  summary?: { active: number; pending: number; inactive: number; total: number };
  companies?: PartnerCompany[];
}

export const PartnerDashboardPanel: React.FC = () => {
  const [data, setData] = useState<PartnerPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    const { data: sessionData } = await supabase.auth.getSession();
    if (!sessionData.session) throw new Error('Not authenticated');
    const res = await fetch(apiUrl('/api/v1/subscriptions/my-partner'), {
      headers: { Authorization: `Bearer ${sessionData.session.access_token}` },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to load partner dashboard');
    }
    setData(await res.json());
  }, []);

  useEffect(() => {
    setLoading(true);
    load()
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false));
  }, [load]);

  const copyCode = async () => {
    const code = data?.partner?.code;
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Could not copy code');
    }
  };

  if (loading) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-gray-600">Loading partner dashboard…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-sm text-red-600">{error}</p>
      </section>
    );
  }

  if (!data?.is_partner || !data.partner) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-gray-600">
          Your account is not linked to a marketing partner profile. Ask the Augusta admin to
          create a referral partner with your login email.
        </p>
      </section>
    );
  }

  const { partner, summary, companies } = data;
  const rows = companies || [];

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm space-y-8">
      <header>
        <p className="text-xs font-semibold uppercase tracking-widest text-[#9a7a35]">Partner</p>
        <h2 className="mt-2 text-3xl font-semibold text-gray-900">{partner.name}</h2>
        <p className="mt-2 text-sm text-gray-600">
          Firms enter your referral code at Company checkout. The same code works for every company
          you bring.
        </p>
      </header>

      <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-gray-50 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Your referral code</p>
          <p className="mt-1 font-mono text-2xl font-semibold text-gray-900">{partner.code}</p>
          {partner.commission_note && (
            <p className="mt-2 text-sm text-gray-600">{partner.commission_note}</p>
          )}
        </div>
        <button type="button" onClick={copyCode} className="augusta-button-primary justify-center">
          {copied ? 'Copied' : 'Copy code'}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        {[
          { label: 'Active', value: summary?.active ?? 0 },
          { label: 'Pending', value: summary?.pending ?? 0 },
          { label: 'Inactive', value: summary?.inactive ?? 0 },
          { label: 'Total', value: summary?.total ?? 0 },
        ].map((s) => (
          <div key={s.label} className="rounded-xl border border-gray-200 px-4 py-3">
            <p className="text-xs uppercase tracking-wide text-gray-500">{s.label}</p>
            <p className="mt-1 text-2xl font-semibold text-gray-900">{s.value}</p>
          </div>
        ))}
      </div>

      <div>
        <h3 className="mb-3 text-lg font-semibold text-gray-900">Companies under your code</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-2 pr-4">Company</th>
                <th className="py-2 pr-4">Plan</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Owner</th>
                <th className="py-2 pr-4">Seats</th>
                <th className="py-2 pr-4">Since</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4 font-medium text-gray-900">{c.name}</td>
                  <td className="py-2 pr-4">{c.plan_label}</td>
                  <td className="py-2 pr-4 capitalize">{c.status}</td>
                  <td className="py-2 pr-4 text-gray-600">{c.owner_email || '—'}</td>
                  <td className="py-2 pr-4">
                    {c.seats_used}/{c.max_seats}
                  </td>
                  <td className="py-2 pr-4 text-gray-600">
                    {c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 text-gray-500">
                    No companies yet — share your code when a firm buys Company Small or Large.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-gray-500">
          Commission is settled outside Augusta while a company stays active.
        </p>
      </div>
    </section>
  );
};

export default PartnerDashboardPanel;
