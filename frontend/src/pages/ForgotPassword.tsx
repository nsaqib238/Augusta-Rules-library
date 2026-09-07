import React, { useState, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import ReCAPTCHA from 'react-google-recaptcha';
import { supabase } from '../lib/supabase';
import { BrandLogo } from '../components/BrandLogo';
import { supportMailto } from '../lib/appConfig';

const ForgotPassword: React.FC = () => {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const recaptchaRef = useRef<ReCAPTCHA>(null);
  const navigate = useNavigate();

  const RECAPTCHA_SITE_KEY = process.env.REACT_APP_RECAPTCHA_SITE_KEY || '';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    if (RECAPTCHA_SITE_KEY && !captchaToken) {
      setError('Please complete the CAPTCHA verification');
      setLoading(false);
      return;
    }

    try {
      const redirectTo = `${window.location.origin}/reset-password`;
      const { error: resetError } = await supabase.auth.resetPasswordForEmail(email.trim().toLowerCase(), {
        redirectTo,
      });

      if (resetError) {
        setError(resetError.message);
        if (recaptchaRef.current) {
          recaptchaRef.current.reset();
          setCaptchaToken(null);
        }
        return;
      }

      setSuccess(
        'If an account exists for that email, a reset link has been sent. Check your inbox (and spam), then follow the link to choose a new password.'
      );
    } catch {
      setError('An unexpected error occurred');
      if (recaptchaRef.current) {
        recaptchaRef.current.reset();
        setCaptchaToken(null);
      }
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
        <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Reset password</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          Enter the email for your Augusta Search account. We will send a secure link to set a new password.
        </p>

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

        {!success && (
          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div className="text-left text-sm">
              <label htmlFor="email" className="text-gray-700">
                Email
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="augusta-input mt-1"
                placeholder="you@example.com"
                required
                autoComplete="email"
              />
            </div>

            {RECAPTCHA_SITE_KEY && (
              <div className="flex justify-center">
                <ReCAPTCHA
                  ref={recaptchaRef}
                  sitekey={RECAPTCHA_SITE_KEY}
                  onChange={(token) => {
                    setCaptchaToken(token);
                    if (token) setError('');
                  }}
                  theme="light"
                />
              </div>
            )}

            <button
              type="submit"
              disabled={loading || (RECAPTCHA_SITE_KEY ? !captchaToken : false)}
              className="augusta-button-primary mt-2 w-full justify-center py-3"
            >
              {loading ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        )}

        <div className="mt-6 flex flex-col gap-2 text-center text-sm text-slate-600">
          <button
            type="button"
            onClick={() => navigate('/login')}
            className="font-semibold text-[#9a7a35] hover:underline"
          >
            Back to sign in
          </button>
          <p className="text-xs text-gray-500">
            Need help?{' '}
            <a href={supportMailto()} className="font-semibold text-[#9a7a35] hover:underline">
              Contact support
            </a>
          </p>
          <p className="text-xs text-gray-400">
            Or <Link to="/signup" className="text-[#9a7a35] hover:underline">create an account</Link>
          </p>
        </div>
      </div>
    </div>
  );
};

export default ForgotPassword;
