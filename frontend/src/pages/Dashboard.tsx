import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { supabase } from '../lib/supabase';
import LibraryQaPanel from '../components/LibraryQaPanel';
import DesignCompliancePanel from '../components/DesignCompliancePanel';
import ProjectOverview from '../components/admin/ProjectOverview';
import EnhancedAdminDashboard from '../components/admin/EnhancedAdminDashboard';
import DocumentRegistryPanel from '../components/admin/DocumentRegistryPanel';
import DatabaseInspector from '../components/admin/DatabaseInspector';
import { OrphanCleanupPanel } from '../components/admin/OrphanCleanupPanel';
import UserDocumentManager from '../components/admin/UserDocumentManager';
import SystemAnalytics from '../components/admin/SystemAnalytics';
import AdminRagLabPanel from '../components/admin/AdminRagLabPanel';
import LibraryAdminPanel from '../components/admin/LibraryAdminPanel';
import { PasscodeManagementPanel } from '../components/admin/PasscodeManagementPanel';
import { CompanyReferralAdminPanel } from '../components/admin/CompanyReferralAdminPanel';
import { CompanySeatsPanel } from '../components/CompanySeatsPanel';
import { PartnerDashboardPanel } from '../components/PartnerDashboardPanel';
import StripeSubscriptionDetails from '../components/subscription/StripeSubscriptionDetails';
import { useSubscription } from '../hooks/useSubscription';
import { BrandLogo } from '../components/BrandLogo';
import { isAdminEmailAllowlisted } from '../lib/adminAllowlist';
import { apiUrl } from '../lib/api';

type DashboardTab =
  | 'company'
  | 'partner'
  | 'qa'
  | 'design'
  | 'admin-overview'
  | 'admin-ingestion'
  | 'admin-library'
  | 'admin-doc-registry'
  | 'admin-analytics'
  | 'admin-database'
  | 'admin-users'
  | 'admin-orphans'
  | 'admin-rag-lab'
  | 'admin-passcodes'
  | 'admin-companies';

interface UserProfile {
  id: string;
  email: string;
  role: string;
  full_name: string;
}

const Dashboard: React.FC = () => {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<DashboardTab>('qa');
  const [showStripeDetails, setShowStripeDetails] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const { subscription, usageStats, createBillingPortalSession } = useSubscription();
  const [isReferralPartner, setIsReferralPartner] = useState(false);

  const isPasscodeProfessional =
    usageStats?.account_type === 'professional' && usageStats?.subscription_source === 'passcode';
  const planLabel =
    isPasscodeProfessional && usageStats?.access_expires_at
      ? `Professional (passcode until ${new Date(usageStats.access_expires_at).toLocaleDateString()})`
      : usageStats?.account_type === 'professional'
        ? 'Professional'
        : 'Sole (library Q&A)';

  const isAdminUser =
    isAdminEmailAllowlisted(user?.email || profile?.email) ||
    profile?.role === 'admin' ||
    profile?.role === 'engineer' ||
    profile?.role === 'inspector';

  const isSole =
    usageStats?.account_type === 'sole' ||
    usageStats?.account_type === 'free' ||
    !usageStats?.account_type;

  const userTabs: Array<{ id: DashboardTab; label: string; disabled?: boolean }> = [
    ...(isReferralPartner ? [{ id: 'partner' as DashboardTab, label: '🤝 Partner' }] : []),
    { id: 'qa', label: '💬 Q&A' },
    { id: 'design', label: '📋 Design Compliance' },
  ];

  const hasActiveSubscription =
    !!subscription &&
    (subscription.status === 'active' ||
      subscription.stripe_status === 'active' ||
      subscription.status === 'trialing');

  const planMenuLabel =
    subscription &&
    (subscription.status === 'active' || subscription.stripe_status === 'active')
      ? 'Manage Plan'
      : 'Upgrade Plan';

  useEffect(() => {
    if (!accountMenuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAccountMenuOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [accountMenuOpen]);

  // Idempotent welcome email (covers email-confirm signup and older accounts)
  useEffect(() => {
    if (!user?.id) return;
    let cancelled = false;
    (async () => {
      try {
        const { data: sessionData } = await supabase.auth.getSession();
        const token = sessionData.session?.access_token;
        if (!token || cancelled) return;
        await fetch(apiUrl('/api/v1/account/welcome-email'), {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
        });
      } catch {
        // non-fatal
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  const openBillingPortal = async () => {
    setAccountMenuOpen(false);
    if (!hasActiveSubscription) {
      navigate('/pricing');
      return;
    }
    try {
      const portalUrl = await createBillingPortalSession(`${window.location.origin}/dashboard`);
      if (portalUrl) {
        window.open(portalUrl, '_blank');
      }
    } catch (error) {
      console.error('Failed to open billing portal:', error);
    }
  };

  const adminTabs: Array<{ id: DashboardTab; label: string; disabled?: boolean }> = [
    { id: 'admin-overview', label: '📊 Overview' },
    { id: 'admin-ingestion', label: '⚙️ Ingestion Control' },
    { id: 'admin-library', label: '📚 Library' },
    { id: 'admin-rag-lab', label: '🧪 RAG Search Lab' },
    { id: 'admin-passcodes', label: '🎟️ Passcodes' },
    { id: 'admin-companies', label: '🏢 Companies' },
    { id: 'admin-doc-registry', label: '📁 Document Registry' },
    { id: 'admin-analytics', label: '📈 Usage & Cost' },
    { id: 'admin-database', label: '🗄️ Database' },
    { id: 'admin-users', label: '👥 User Documents' },
    { id: 'admin-orphans', label: '🧹 Orphan Cleanup' },
    ...userTabs,
  ];

  const navTabs = isAdminUser ? adminTabs : userTabs;

  useEffect(() => {
    if (!user?.id) {
      setLoading(false);
      return;
    }

    void (async () => {
      try {
        const { data, error } = await supabase
          .from('profiles')
          .select('id, email, role, full_name')
          .eq('id', user.id)
          .single();

        if (error && error.code !== 'PGRST116') {
          setProfile(null);
          return;
        }

        if (data) {
          setProfile(data);
        } else {
          setProfile({
            id: user.id,
            email: user.email || '',
            full_name: user.user_metadata?.full_name || user.email?.split('@')[0] || 'User',
            role: 'user',
          });
        }
      } catch {
        setProfile(null);
      } finally {
        setLoading(false);
      }
    })();
  }, [user?.id, user?.email, user?.user_metadata?.full_name]);

  useEffect(() => {
    if (!user?.id) {
      setIsReferralPartner(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const { data: sessionData } = await supabase.auth.getSession();
        if (!sessionData.session) {
          if (!cancelled) setIsReferralPartner(false);
          return;
        }
        const res = await fetch(apiUrl('/api/v1/subscriptions/my-partner'), {
          headers: { Authorization: `Bearer ${sessionData.session.access_token}` },
        });
        if (!res.ok) {
          if (!cancelled) setIsReferralPartner(false);
          return;
        }
        const body = await res.json();
        if (!cancelled) setIsReferralPartner(!!body.is_partner);
      } catch {
        if (!cancelled) setIsReferralPartner(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  useEffect(() => {
    if (isAdminUser) {
      setActiveTab('admin-overview');
    } else {
      setActiveTab('qa');
    }
  }, [isAdminUser]);

  const handleLogout = async () => {
    await supabase.auth.signOut();
    setUser(null);
    window.location.href = '/login';
  };

  if (loading) {
    return (
      <div className="augusta-page-shell flex min-h-screen items-center justify-center">
        <div className="augusta-card flex items-center gap-4 px-6 py-5">
          <div className="h-10 w-10 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]" />
          <div>
            <p className="text-sm font-semibold text-slate-900">Preparing Augusta Search</p>
            <p className="text-xs text-slate-500">Loading your secure workspace...</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <main className="augusta-page-shell min-h-screen">
      <header className="sticky top-0 z-50 border-b border-white/60 bg-[#fbf7ef]/82 backdrop-blur-2xl">
        <div className="mx-auto max-w-[1500px] px-4 sm:px-6 lg:px-8">
          <div className="flex min-h-[84px] flex-col gap-4 py-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 items-center gap-4">
              <BrandLogo variant="header" />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <h1 className="text-lg font-semibold tracking-tight text-slate-950 sm:text-xl">
                    Premium compliance intelligence
                  </h1>
                  <span className="rounded-full border border-[#d6bf82]/70 bg-[#fff7df] px-3 py-1 text-xs font-semibold text-[#7c5f1e]">
                    NCC &amp; SIR · Australia
                  </span>
                </div>
              </div>
            </div>

            <div className="relative flex items-center justify-end" ref={accountMenuRef}>
              <button
                type="button"
                aria-haspopup="menu"
                aria-expanded={accountMenuOpen}
                onClick={() => setAccountMenuOpen((open) => !open)}
                className="flex items-center gap-3 rounded-full border border-white/70 bg-white/70 py-1.5 pl-2 pr-3 shadow-sm transition hover:bg-white"
              >
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-[#0b1220] to-[#334155] text-sm font-semibold uppercase text-white shadow-lg shadow-slate-900/20">
                  {(profile?.full_name || user?.email || 'N')[0]}
                </div>
                <div className="hidden max-w-[180px] truncate text-sm font-semibold text-slate-700 sm:block">
                  {profile?.full_name || user?.email}
                </div>
                <svg
                  className={`h-4 w-4 text-slate-500 transition ${accountMenuOpen ? 'rotate-180' : ''}`}
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <path
                    fillRule="evenodd"
                    d="M5.23 7.21a.75.75 0 011.06.02L10 11.17l3.71-3.94a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>

              {accountMenuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-56 overflow-hidden rounded-2xl border border-white/80 bg-white/95 py-1 shadow-[0_18px_50px_rgba(15,23,42,0.16)] backdrop-blur-xl"
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition hover:bg-slate-50 hover:text-slate-950"
                    onClick={() => {
                      setAccountMenuOpen(false);
                      window.open('/about-app', '_blank');
                    }}
                  >
                    Product Overview
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition hover:bg-slate-50 hover:text-slate-950"
                    onClick={openBillingPortal}
                  >
                    {planMenuLabel}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition hover:bg-slate-50 hover:text-slate-950"
                    onClick={() => {
                      setShowStripeDetails((v) => !v);
                      setAccountMenuOpen(false);
                    }}
                  >
                    {showStripeDetails ? 'Hide Billing' : 'Billing Status'}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition hover:bg-slate-50 hover:text-slate-950"
                    onClick={() => {
                      setActiveTab('company');
                      setAccountMenuOpen(false);
                    }}
                  >
                    Company
                  </button>
                  <div className="my-1 border-t border-slate-100" />
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition hover:bg-slate-50 hover:text-slate-950"
                    onClick={() => {
                      setAccountMenuOpen(false);
                      void handleLogout();
                    }}
                  >
                    Logout
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] px-4 pb-10 pt-8 sm:px-6 lg:px-8">
        <section className="augusta-glass relative mb-6 overflow-hidden rounded-[34px] px-6 py-7 sm:px-8">
          <div className="absolute right-0 top-0 h-40 w-40 rounded-full bg-[#c9a45c]/20 blur-3xl" />
          <div className="relative">
            <div className="max-w-3xl">
              <p className="augusta-eyebrow mb-3">Country library</p>
              <h2 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
                Ask grounded questions over the Australian rules library.
              </h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
                Pick a country, open a document type, then ask against one document. No user PDF upload in this product.
              </p>
              {usageStats && (
                <p className="mt-4 inline-flex items-center rounded-full border border-[#c9a45c]/40 bg-[#f1ddab]/30 px-4 py-1.5 text-sm font-medium text-slate-800">
                  Current plan: {planLabel}
                </p>
              )}
              {isSole && (
                <p className="mt-3 text-sm text-slate-600">
                  Have an admin passcode or company join code?{' '}
                  <button
                    type="button"
                    onClick={() => navigate('/pricing#passcode')}
                    className="font-semibold text-[#9a7a35] underline-offset-2 hover:underline"
                  >
                    Redeem or join on the pricing page
                  </button>
                  .
                </p>
              )}
            </div>
          </div>
        </section>

        {showStripeDetails && (
          <div className="mb-6">
            <StripeSubscriptionDetails />
          </div>
        )}

        <nav className="mb-6 flex gap-3 overflow-x-auto rounded-[26px] border border-white/70 bg-white/55 p-2 shadow-sm backdrop-blur-xl">
          {navTabs.map((tab) => {
            const isActive = activeTab === tab.id;
            const label = tab.label.replace(/^[^\w]+ /, '');
            return (
              <button
                key={tab.id}
                type="button"
                disabled={tab.disabled}
                onClick={() => !tab.disabled && setActiveTab(tab.id)}
                className={`whitespace-nowrap rounded-full px-4 py-2.5 text-sm font-semibold transition ${
                  tab.disabled
                    ? 'cursor-not-allowed opacity-40'
                    : isActive
                      ? 'bg-[#0b1220] text-white shadow-[0_14px_30px_rgba(15,23,42,0.22)]'
                      : 'text-slate-600 hover:bg-white/80 hover:text-slate-950'
                }`}
              >
                {label}
              </button>
            );
          })}
        </nav>

        <div className="augusta-card p-4 sm:p-6">
          {activeTab === 'company' && <CompanySeatsPanel />}
          {activeTab === 'partner' && isReferralPartner && <PartnerDashboardPanel />}
          {activeTab === 'admin-overview' && isAdminUser && (
            <div className="space-y-8">
              <ProjectOverview />
              <EnhancedAdminDashboard
                mode="overview"
                hideNavigation
                compactHeader
                title="Ingestion KPIs"
                description="Live stats pulled from ingestion services."
              />
            </div>
          )}
          {activeTab === 'admin-ingestion' && isAdminUser && (
            <div className="space-y-8">
              <EnhancedAdminDashboard
                mode="queue"
                hideNavigation
                compactHeader
                title="Ingestion Queue"
                description="Monitor ingestion jobs and queue priorities."
              />
              <EnhancedAdminDashboard
                mode="processing"
                hideNavigation
                compactHeader
                title="Chunk Processing"
                description="Manual pipeline controls for chunking and table upload."
              />
            </div>
          )}
          {activeTab === 'admin-rag-lab' && isAdminUser && <AdminRagLabPanel />}
          {activeTab === 'admin-passcodes' && isAdminUser && <PasscodeManagementPanel />}
          {activeTab === 'admin-companies' && isAdminUser && <CompanyReferralAdminPanel />}
          {activeTab === 'admin-library' && isAdminUser && <LibraryAdminPanel />}
          {activeTab === 'admin-doc-registry' && isAdminUser && <DocumentRegistryPanel />}
          {activeTab === 'admin-analytics' && isAdminUser && <SystemAnalytics />}
          {activeTab === 'admin-database' && isAdminUser && <DatabaseInspector />}
          {activeTab === 'admin-users' && isAdminUser && <UserDocumentManager />}
          {activeTab === 'admin-orphans' && isAdminUser && <OrphanCleanupPanel />}
          {activeTab === 'qa' && <LibraryQaPanel />}
          {activeTab === 'design' &&
            (isSole ? (
              <div className="rounded-[26px] border border-[#d6bf82] bg-[#fffaf0] p-8">
                <p className="augusta-eyebrow mb-2">Professional</p>
                <h2 className="text-2xl font-semibold text-slate-950">Design Compliance</h2>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
                  Plan a project brief against multiple library documents and generate a design review report. This
                  tool is included on Professional.
                </p>
                <button
                  type="button"
                  onClick={() => navigate('/pricing')}
                  className="mt-5 rounded-full bg-[#0b1220] px-5 py-2.5 text-sm font-semibold text-white"
                >
                  Upgrade on Pricing
                </button>
              </div>
            ) : (
              <DesignCompliancePanel />
            ))}
        </div>
      </div>
    </main>
  );
};

export default Dashboard;
