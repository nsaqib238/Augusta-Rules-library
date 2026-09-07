import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabase';
import { Mail, Lock, Eye, EyeOff, User } from 'lucide-react';
import { BrandLogo } from '../components/BrandLogo';
import { apiUrl } from '../lib/api';

async function requestWelcomeEmail(accessToken: string): Promise<void> {
  try {
    await fetch(apiUrl('/api/v1/account/welcome-email'), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
    });
  } catch (err) {
    console.warn('Welcome email request failed:', err);
  }
}

const SignupPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    // Validation
    if (!fullName.trim()) {
      setError('Full name is required');
      setLoading(false);
      return;
    }

    if (!email.trim()) {
      setError('Email is required');
      setLoading(false);
      return;
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters long');
      setLoading(false);
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      setLoading(false);
      return;
    }

    try {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/dashboard`,
          data: {
            full_name: fullName
          }
        }
      });

      if (error) {
        if (error.message.includes('Anonymous sign-ins are disabled')) {
          setError('Please use email and password to create an account.');
        } else {
          setError(error.message);
        }
        return;
      }

      if (data.user) {
        // Profile is created by DB trigger handle_new_user().
        // Only try a client upsert when we have a session (email confirm off).
        // Never fail signup with a scary error if the trigger already created the row.
        if (data.session) {
          const { error: profileError } = await supabase.from('profiles').upsert(
            {
              id: data.user.id,
              email: email.trim().toLowerCase(),
              full_name: fullName.trim(),
              updated_at: new Date().toISOString(),
            },
            { onConflict: 'id' }
          );
          if (profileError) {
            // Log full PostgREST error for support / DevTools
            console.error('Profile upsert after signup:', profileError.message, profileError);
          }
          await requestWelcomeEmail(data.session.access_token);
          setSuccess('Account created successfully! Redirecting…');
          navigate('/dashboard');
        } else {
          setSuccess(
            'Account created successfully! Please check your email for the verification link, then sign in.'
          );
        }
      }
    } catch (err) {
      console.error('Signup error:', err);
      setError('An unexpected error occurred');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="augusta-page-shell flex min-h-screen items-center justify-center px-4 py-10">
      <div className="augusta-glass grid w-full max-w-6xl overflow-hidden rounded-[36px] lg:grid-cols-[1.05fr,0.95fr]">
        <section className="relative overflow-hidden bg-[#0b1220] px-8 py-10 text-white sm:px-12 sm:py-14">
          <div className="absolute -left-24 top-10 h-64 w-64 rounded-full bg-[#c9a45c]/25 blur-3xl" />
          <div className="absolute bottom-0 right-0 h-80 w-80 rounded-full bg-blue-400/10 blur-3xl" />
          <div className="relative flex min-h-[620px] flex-col justify-between">
            <div>
              <div className="flex items-center gap-4">
                <BrandLogo variant="auth" />
                <div>
                  <div className="mt-1 text-xs text-white/55">Compliance library</div>
                </div>
              </div>

              <div className="mt-20 max-w-lg space-y-6">
                <p className="text-[12px] font-semibold uppercase tracking-[0.28em] text-[#f1ddab]">Codes, standards &amp; rules</p>
                <h2 className="text-4xl font-semibold leading-tight tracking-tight text-white sm:text-5xl">
                  Get started with the compliance library.
                </h2>
                <p className="text-sm leading-7 text-slate-300">
                  Create your account and ask grounded questions over shared codes, standards, and rules.
                </p>
              </div>
            </div>

            <div className="grid gap-3 text-sm text-slate-200 sm:grid-cols-3">
              {['Secure sign-in', 'Stripe plans', 'Evidence-first AI'].map((item) => (
                <div key={item} className="rounded-2xl border border-white/10 bg-white/[0.06] px-4 py-3 backdrop-blur">
                  {item}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="flex items-center justify-center bg-white/82 px-6 py-10 sm:px-10">
          <div className="w-full max-w-sm">
            <div className="mb-6">
              <p className="augusta-eyebrow mb-3">Create workspace</p>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Start with Augusta Search</h1>
              <p className="mt-2 text-sm leading-6 text-slate-500">Set up your account for compliance search and Q&amp;A.</p>
            </div>

            {success && (
              <div className="mb-4 rounded-2xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
                {success}
              </div>
            )}

            {error && (
              <div className="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="text-sm text-left">
                <label htmlFor="fullName" className="text-gray-700">
                  Full name
                </label>
                <div className="relative mt-1">
                  <span className="absolute inset-y-0 left-3 flex items-center text-gray-400">
                    <User className="h-4 w-4" />
                  </span>
                  <input
                    id="fullName"
                    type="text"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    className="augusta-input pl-10"
                    placeholder="Enter your name"
                    required
                  />
                </div>
              </div>

              <div className="text-sm text-left">
                <label htmlFor="email" className="text-gray-700">
                  Email
                </label>
                <div className="relative mt-1">
                  <span className="absolute inset-y-0 left-3 flex items-center text-gray-400">
                    <Mail className="h-4 w-4" />
                  </span>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="augusta-input pl-10"
                    placeholder="you@example.com"
                    required
                  />
                </div>
              </div>

              <div className="text-sm text-left">
                <label htmlFor="password" className="text-gray-700">
                  Password
                </label>
                <div className="relative mt-1">
                  <span className="absolute inset-y-0 left-3 flex items-center text-gray-400">
                    <Lock className="h-4 w-4" />
                  </span>
                  <input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="augusta-input pl-10 pr-10"
                    placeholder="Create a password"
                    required
                  />
                  <button
                    type="button"
                    className="absolute inset-y-0 right-3 flex items-center text-gray-400"
                    onClick={() => setShowPassword((prev) => !prev)}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="text-sm text-left">
                <label htmlFor="confirmPassword" className="text-gray-700">
                  Confirm password
                </label>
                <div className="relative mt-1">
                  <span className="absolute inset-y-0 left-3 flex items-center text-gray-400">
                    <Lock className="h-4 w-4" />
                  </span>
                  <input
                    id="confirmPassword"
                    type={showConfirmPassword ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="augusta-input pl-10 pr-10"
                    placeholder="Confirm your password"
                    required
                  />
                  <button
                    type="button"
                    className="absolute inset-y-0 right-3 flex items-center text-gray-400"
                    onClick={() => setShowConfirmPassword((prev) => !prev)}
                  >
                    {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="augusta-button-primary mt-2 w-full justify-center py-3"
              >
                {loading ? 'Creating account...' : 'Create account'}
              </button>
            </form>

            <p className="mt-6 text-center text-xs text-gray-500">
              Already have an account?{' '}
              <button onClick={() => navigate('/login')} className="font-semibold text-[#9a7a35] hover:underline">
                Sign in instead
              </button>
            </p>

            <p className="mt-4 text-center text-xs text-gray-500">
              Have an access code? Sign in, then redeem it on the{' '}
              <a href="/pricing#passcode" className="font-semibold text-[#9a7a35] hover:underline">
                pricing page
              </a>
              .
            </p>
          </div>
        </section>
      </div>
    </div>
  );
};

export default SignupPage;
