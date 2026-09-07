/**
 * Company seat overview; owners can copy join code and remove employees.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { apiUrl } from '../lib/api';

interface CompanyInfo {
  id: string;
  name: string;
  join_code: string | null;
  max_seats: number;
  seats_used: number;
  status: string;
  role: string;
}

interface CompanyMember {
  user_id: string;
  role: string;
  joined_at?: string | null;
  email?: string | null;
  full_name?: string | null;
  is_self?: boolean;
}

export const CompanySeatsPanel: React.FC = () => {
  const [company, setCompany] = useState<CompanyInfo | null>(null);
  const [members, setMembers] = useState<CompanyMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      setError('Not authenticated');
      return;
    }
    const res = await fetch(apiUrl('/api/v1/subscriptions/my-company'), {
      headers: { Authorization: `Bearer ${session.access_token}` },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : 'Failed to load company';
      throw new Error(detail);
    }
    setCompany(data.company ?? null);
    setMembers(Array.isArray(data.members) ? data.members : []);
    if (!data.company) {
      setError(null);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load company');
      } finally {
        setLoading(false);
      }
    })();
  }, [load]);

  const copyJoinCode = async () => {
    if (!company?.join_code) return;
    try {
      await navigator.clipboard.writeText(company.join_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Could not copy join code');
    }
  };

  const removeMember = async (member: CompanyMember) => {
    const label = member.email || member.full_name || member.user_id;
    if (!window.confirm(`Remove ${label} from your company seats?\n\nThey will lose Professional access but keep their Augusta login.`)) {
      return;
    }
    setRemovingId(member.user_id);
    setActionMessage(null);
    setError(null);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error('Not authenticated');
      const res = await fetch(apiUrl('/api/v1/subscriptions/my-company/remove-member'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ user_id: member.user_id }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Failed to remove member');
      setActionMessage(data.message || 'Member removed.');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove member');
    } finally {
      setRemovingId(null);
    }
  };

  if (loading) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-gray-600">Loading company…</p>
      </section>
    );
  }

  if (error && !company) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-red-600">{error}</p>
      </section>
    );
  }

  if (!company) {
    return (
      <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <h2 className="text-2xl font-semibold text-gray-900">Company seats</h2>
        <p className="mt-3 text-sm text-gray-600">
          You are not on a company plan. Buy Company Small or Large on the pricing page, or join with a company join code.
        </p>
      </section>
    );
  }

  const isOwner = company.role === 'owner';

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm space-y-8">
      <header>
        <p className="text-xs font-semibold uppercase tracking-widest text-blue-600">Company</p>
        <h2 className="mt-2 text-3xl font-semibold text-gray-900">{company.name}</h2>
        <p className="mt-2 text-sm text-gray-600">
          Role: {company.role} · Status: {company.status}
        </p>
      </header>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {actionMessage && <p className="text-sm text-green-700">{actionMessage}</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Seats used</p>
          <p className="mt-2 text-3xl font-semibold text-gray-900">
            {company.seats_used} / {company.max_seats}
          </p>
        </div>
        {isOwner && company.join_code && (
          <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Join code</p>
            <p className="mt-2 font-mono text-xl font-semibold tracking-wide text-gray-900">
              {company.join_code}
            </p>
            <button
              type="button"
              onClick={copyJoinCode}
              className="mt-3 text-sm font-semibold text-blue-700 hover:underline"
            >
              {copied ? 'Copied' : 'Copy join code'}
            </button>
            <p className="mt-2 text-xs text-gray-500">
              Share this with staff so each person can join with their own login. Do not share marketing referral codes here.
            </p>
          </div>
        )}
      </div>

      {isOwner && (
        <div>
          <h3 className="text-lg font-semibold text-gray-900 mb-3">Members</h3>
          <p className="mb-3 text-sm text-gray-600">
            Remove an employee to free a seat. They keep their account but lose company Professional access.
          </p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b text-left text-gray-500">
                  <th className="py-2 pr-4">Name</th>
                  <th className="py-2 pr-4">Email</th>
                  <th className="py-2 pr-4">Role</th>
                  <th className="py-2 pr-4">Action</th>
                </tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.user_id} className="border-b border-gray-100">
                    <td className="py-2 pr-4 font-medium text-gray-900">
                      {m.full_name || '—'}
                      {m.is_self ? ' (you)' : ''}
                    </td>
                    <td className="py-2 pr-4 text-gray-600">{m.email || '—'}</td>
                    <td className="py-2 pr-4">{m.role}</td>
                    <td className="py-2 pr-4">
                      {m.role === 'owner' || m.is_self ? (
                        <span className="text-gray-400">—</span>
                      ) : (
                        <button
                          type="button"
                          disabled={removingId === m.user_id}
                          onClick={() => void removeMember(m)}
                          className="text-sm font-semibold text-red-700 hover:underline disabled:opacity-50"
                        >
                          {removingId === m.user_id ? 'Removing…' : 'Remove'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {members.length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-4 text-gray-500">No members yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
};

export default CompanySeatsPanel;
