/**
 * Pricing – Sole, Professional, Company Small/Large + passcode / join company.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useSubscription } from '../hooks/useSubscription';

const formatAud = (amount: number) =>
  new Intl.NumberFormat('en-AU', {
    style: 'currency',
    currency: 'AUD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount);

const Pricing: React.FC = () => {
  const { user } = useAuth();
  const { createCheckoutSession, redeemPasscode, joinCompany } = useSubscription();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState<string | null>(null);
  const [passcode, setPasscode] = useState('');
  const [passcodeLoading, setPasscodeLoading] = useState(false);
  const [passcodeMessage, setPasscodeMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [joinCode, setJoinCode] = useState('');
  const [joinLoading, setJoinLoading] = useState(false);
  const [joinMessage, setJoinMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [referralCode, setReferralCode] = useState('');
  const [companyName, setCompanyName] = useState('');

  const plans = [
    {
      id: 'sole',
      name: 'Sole',
      price: 0,
      interval: 'month',
      features: [
        'Q&A on NCC (National Construction Code) volumes',
        'Q&A on Service & Installation Rules (SIR) editions',
        'Cited, evidence-bound answers',
      ],
      popular: false,
      isFree: true,
      isCompany: false,
    },
    {
      id: 'professional',
      name: 'Professional',
      price: 49,
      interval: 'month',
      features: [
        'Full NCC and SIR Q&A access',
        'Higher usage limits than Sole',
        'Priority support',
      ],
      popular: true,
      isFree: false,
      isCompany: false,
    },
    {
      id: 'company_small',
      name: 'Company Small',
      price: 599,
      interval: 'month',
      features: [
        'Up to 25 seats (owner + staff)',
        'NCC and SIR Q&A for every seat',
        'Each employee uses their own login',
        'All Professional features for every seat',
      ],
      popular: false,
      isFree: false,
      isCompany: true,
    },
    {
      id: 'company_large',
      name: 'Company Large',
      price: 1099,
      interval: 'month',
      features: [
        'Up to 50 seats (owner + staff)',
        'NCC and SIR Q&A for every seat',
        'Each employee uses their own login',
        'All Professional features for every seat',
      ],
      popular: false,
      isFree: false,
      isCompany: true,
    },
  ];

  const handleSubscribe = async (planId: string) => {
    if (!user) {
      navigate('/login', { state: { returnUrl: '/pricing' } });
      return;
    }
    if (planId === 'sole') {
      navigate('/dashboard');
      return;
    }
    try {
      setLoading(true);
      setSelectedPlan(planId);
      const successUrl = `${window.location.origin}/subscription-success`;
      const cancelUrl = `${window.location.origin}/pricing`;
      const isCompany = planId === 'company_small' || planId === 'company_large';
      const checkoutUrl = await createCheckoutSession(
        planId,
        successUrl,
        cancelUrl,
        isCompany
          ? {
              referralCode: referralCode || undefined,
              companyName: companyName || undefined,
            }
          : undefined
      );
      if (checkoutUrl) {
        window.location.href = checkoutUrl;
      } else {
        alert('Failed to start checkout. Please try again.');
      }
    } catch (error) {
      console.error('Checkout error:', error);
      alert('An error occurred. Please try again.');
    } finally {
      setLoading(false);
      setSelectedPlan(null);
    }
  };

  const handleRedeemPasscode = async () => {
    if (!user) {
      navigate('/login', { state: { returnUrl: '/pricing' } });
      return;
    }
    if (!passcode.trim()) {
      setPasscodeMessage({ type: 'error', text: 'Please enter a passcode.' });
      return;
    }
    setPasscodeMessage(null);
    setPasscodeLoading(true);
    try {
      const result = await redeemPasscode(passcode);
      if (result.success) {
        setPasscodeMessage({ type: 'success', text: result.message });
        setPasscode('');
        navigate('/dashboard');
      } else {
        setPasscodeMessage({ type: 'error', text: result.message });
      }
    } finally {
      setPasscodeLoading(false);
    }
  };

  const handleJoinCompany = async () => {
    if (!user) {
      navigate('/login', { state: { returnUrl: '/pricing#join-company' } });
      return;
    }
    if (!joinCode.trim()) {
      setJoinMessage({ type: 'error', text: 'Please enter a company join code.' });
      return;
    }
    setJoinMessage(null);
    setJoinLoading(true);
    try {
      const result = await joinCompany(joinCode);
      if (result.success) {
        setJoinMessage({ type: 'success', text: result.message });
        setJoinCode('');
        navigate('/dashboard');
      } else {
        setJoinMessage({ type: 'error', text: result.message });
      }
    } finally {
      setJoinLoading(false);
    }
  };

  return (
    <div className="augusta-page-shell min-h-screen py-12">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mb-6">
          <button
            onClick={() => navigate(-1)}
            className="augusta-button-secondary inline-flex items-center"
          >
            <span className="mr-2">←</span>
            Back
          </button>
        </div>
        <div className="augusta-glass mx-auto max-w-4xl rounded-[34px] px-6 py-10 text-center">
          <p className="augusta-eyebrow mb-3">Augusta Search pricing</p>
          <h1 className="text-4xl font-semibold tracking-tight text-slate-950">Choose your NCC &amp; SIR plan</h1>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-7 text-slate-600">
            Free Sole access for NCC and SIR Q&amp;A, upgrade to Professional for higher limits, or seat your team on a
            Company plan.
          </p>
        </div>

        <div className="mt-16 grid gap-8 lg:grid-cols-2 max-w-5xl mx-auto">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`relative overflow-hidden rounded-[30px] border bg-white/85 shadow-[0_22px_70px_rgba(15,23,42,0.10)] backdrop-blur-xl ${
                plan.popular ? 'border-[#c9a45c] ring-4 ring-[#f1ddab]/45' : 'border-white/70'
              }`}
            >
              {plan.popular && (
                <div className="absolute right-5 top-5 rounded-full bg-[#0b1220] px-4 py-1 text-sm font-semibold text-[#f1ddab]">
                  Most Popular
                </div>
              )}

              <div className="p-8">
                <h3 className="text-2xl font-semibold tracking-tight text-slate-950">{plan.name}</h3>

                <div className={`mt-4 flex ${plan.isFree ? 'flex-col items-start space-y-1' : 'items-baseline'}`}>
                  <span className="text-5xl font-semibold tracking-tight text-slate-950">{formatAud(plan.price)}</span>
                  <span className="ml-2 text-xl text-slate-500">/{plan.interval}</span>
                </div>

                <ul className="mt-8 space-y-4">
                  {plan.features.map((feature, index) => (
                    <li key={index} className="flex items-start">
                      <svg
                        className="h-6 w-6 flex-shrink-0 text-[#9a7a35]"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M5 13l4 4L19 7"
                        />
                      </svg>
                      <span className="ml-3 text-slate-700">{feature}</span>
                    </li>
                  ))}
                </ul>

                {plan.isCompany && (
                  <div className="mt-6 space-y-3">
                    <input
                      type="text"
                      value={companyName}
                      onChange={(e) => setCompanyName(e.target.value)}
                      placeholder="Company name (optional)"
                      className="augusta-input w-full"
                    />
                    <input
                      type="text"
                      value={referralCode}
                      onChange={(e) => setReferralCode(e.target.value)}
                      placeholder="Referral code (optional)"
                      className="augusta-input w-full"
                    />
                  </div>
                )}

                <button
                  onClick={() => handleSubscribe(plan.id)}
                  disabled={loading && selectedPlan === plan.id}
                  className="augusta-button-primary mt-8 w-full justify-center px-6 py-3"
                >
                  {loading && selectedPlan === plan.id ? 'Loading...' : 'Get Started'}
                </button>
              </div>
            </div>
          ))}
        </div>

        {user && (
          <div className="mt-12 max-w-3xl mx-auto grid gap-6 md:grid-cols-2">
            <div id="passcode" className="augusta-card p-6 scroll-mt-24">
              <h3 className="text-lg font-semibold text-slate-950 mb-2">Have a passcode?</h3>
              <p className="text-sm text-slate-600 mb-4">
                Enter your passcode to get professional access for a limited time.
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={passcode}
                  onChange={(e) => setPasscode(e.target.value)}
                  placeholder="e.g. PILOT-A1B2C3"
                  className="augusta-input flex-1"
                  disabled={passcodeLoading}
                />
                <button
                  onClick={handleRedeemPasscode}
                  disabled={passcodeLoading}
                  className="augusta-button-primary px-4 py-2"
                >
                  {passcodeLoading ? 'Redeeming...' : 'Redeem'}
                </button>
              </div>
              {passcodeMessage && (
                <p className={`mt-3 text-sm ${passcodeMessage.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                  {passcodeMessage.text}
                </p>
              )}
            </div>

            <div id="join-company" className="augusta-card p-6 scroll-mt-24">
              <h3 className="text-lg font-semibold text-slate-950 mb-2">Join a company?</h3>
              <p className="text-sm text-slate-600 mb-4">
                Enter the company join code from your employer (not a marketing referral code).
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={joinCode}
                  onChange={(e) => setJoinCode(e.target.value)}
                  placeholder="e.g. CO-A1B2C3D4"
                  className="augusta-input flex-1"
                  disabled={joinLoading}
                />
                <button
                  onClick={handleJoinCompany}
                  disabled={joinLoading}
                  className="augusta-button-primary px-4 py-2"
                >
                  {joinLoading ? 'Joining...' : 'Join'}
                </button>
              </div>
              {joinMessage && (
                <p className={`mt-3 text-sm ${joinMessage.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                  {joinMessage.text}
                </p>
              )}
            </div>
          </div>
        )}

        <div className="mt-20">
          <h2 className="text-3xl font-semibold text-center text-slate-950">
            Frequently Asked Questions
          </h2>

          <div className="mt-10 max-w-3xl mx-auto space-y-6">
            <div className="augusta-card p-6">
              <h3 className="text-lg font-semibold text-slate-950">
                Can I switch plans later?
              </h3>
              <p className="mt-2 text-slate-600">
                Yes. Sole is free for NCC and SIR Q&amp;A. Upgrade to Professional for higher limits and priority support,
                or choose a Company plan for team seats.
              </p>
            </div>

            <div className="augusta-card p-6">
              <h3 className="text-lg font-semibold text-slate-950">
                How do company seats work?
              </h3>
              <p className="mt-2 text-slate-600">
                The buyer is the owner and counts as one seat. They share a join code with staff. Each person signs in
                with their own account and gets NCC and SIR Q&amp;A access.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Pricing;
