import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabase';
import { BrandLogo } from '../components/BrandLogo';
import { supportMailto } from '../lib/appConfig';

/**
 * Landing page for Supabase recovery links (redirectTo=/reset-password).
 * User must arrive with a recovery session from the email link, then set a new password.
 */
const ResetPassword: React.FC = () => {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (cancelled) return;
      if (event === 'PASSWORD_RECOVERY' || (event === 'SIGNED_IN' && session)) {
        setReady(true);
        setChecking(false);
        setError('');
      }
    });

    // Hash/query tokens are parsed async by the client; also check existing session
    (async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (cancelled) return;
      if (session) {
        setReady(true);
        setChecking(false);
        return;
      }
      // Give detectSessionInUrl a moment after redirect
      window.setTimeout(async () => {
        if (cancelled) return;
        const { data: { session: late } } = await supabase.auth.getSession();
        if (late) {
          setReady(true);
          setChecking(false);
        } else {
          setChecking(false);
          setReady(false);
          setError(
            'This reset link is invalid or has expired. Request a new password reset from the sign-in page.'
          );
        }
      }, 800);
    })();

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

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
      const { error: updateError } = await supabase.auth.updateUser({ password });
      if (updateError) {
        setError(updateError.message);
        return;
      }
      await supabase.auth.signOut();
      setSuccess('Password updated. You can sign in with your new password.');
      window.setTimeout(() => navigate('/login'), 1500);
    } catch {
      setError('An unexpected error occurred');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="augusta-page-shell flex min-h-screen items-center justify-center px-4 py-10">
      <div className="augusta-glass w-full max-w-md overflow-hidden rounded-[36px] bg-white/82 px-6 py-10 sm:px-10">
        <div className="mb-6 flex items-center gap-3">
          <BrandLogo variant="auth" />
        </div>

        <p className="augusta-eyebrow mb-3">Account recovery</p>
        <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Choose a new password</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          Set a new password for your Augusta Search account.
        </p>

        {checking && (
          <p className="mt-6 text-sm text-slate-500">Verifying reset link…</p>
        )}

        {error && (
          <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {success && (
          <div className="mt-4 rounded-2xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
            {success}
          </div>
        )}

        {!checking && ready && !success && (
          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div className="text-left text-sm">
              <label htmlFor="password" className="text-gray-700">
                New password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="augusta-input mt-1"
                placeholder="At least 6 characters"
                required
                autoComplete="new-password"
                minLength={6}
              />
            </div>
            <div className="text-left text-sm">
              <label htmlFor="confirmPassword" className="text-gray-700">
                Confirm password
              </label>
              <input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="augusta-input mt-1"
                placeholder="Re-enter password"
                required
                autoComplete="new-password"
                minLength={6}
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="augusta-button-primary mt-2 w-full justify-center py-3"
            >
              {loading ? 'Saving…' : 'Update password'}
            </button>
          </form>
        )}

        {!checking && !ready && (
          <button
            type="button"
            onClick={() => navigate('/forgot-password')}
            className="augusta-button-primary mt-6 w-full justify-center py-3"
          >
            Request a new reset link
          </button>
        )}

        <p className="mt-6 text-center text-xs text-gray-500">
          Need help?{' '}
          <a href={supportMailto()} className="font-semibold text-[#9a7a35] hover:underline">
            Contact support
          </a>
        </p>
      </div>
    </div>
  );
};

export default ResetPassword;
