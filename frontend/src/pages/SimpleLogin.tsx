import React, { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import ReCAPTCHA from 'react-google-recaptcha';
import { supabase } from '../lib/supabase';
import { useAuth } from '../contexts/AuthContext';
import { BrandLogo } from '../components/BrandLogo';
import { supportMailto } from '../lib/appConfig';
const SimpleLogin: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const recaptchaRef = useRef<ReCAPTCHA>(null);
  const navigate = useNavigate();
  const { setUser } = useAuth();

  const RECAPTCHA_SITE_KEY = process.env.REACT_APP_RECAPTCHA_SITE_KEY || '';

  const handleCaptchaChange = (token: string | null) => {
    setCaptchaToken(token);
    // Clear error when CAPTCHA is completed
    if (token) {
      setError('');
    }
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    // Verify CAPTCHA is completed
    if (RECAPTCHA_SITE_KEY && !captchaToken) {
      setError('Please complete the CAPTCHA verification');
      setLoading(false);
      return;
    }

    try {
      const { data, error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });

      if (error) {
        setError(error.message);
        // Reset CAPTCHA on error
        if (recaptchaRef.current) {
          recaptchaRef.current.reset();
          setCaptchaToken(null);
        }
        return;
      }

      if (data.user) {
        setUser(data.user);
        // All users go to dashboard - admin functionality is available via Admin Input tab
        navigate('/dashboard');
      }
    } catch (err) {
      setError('An unexpected error occurred');
      // Reset CAPTCHA on error
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
      <div className="augusta-glass grid w-full max-w-6xl overflow-hidden rounded-[36px] lg:grid-cols-[1.05fr,0.95fr]">
        <section className="relative overflow-hidden bg-[#0b1220] px-8 py-10 text-white sm:px-12 sm:py-14">
          <div className="absolute -left-24 top-10 h-64 w-64 rounded-full bg-[#c9a45c]/25 blur-3xl" />
          <div className="absolute bottom-0 right-0 h-80 w-80 rounded-full bg-blue-400/10 blur-3xl" />
          <div className="relative flex min-h-[560px] flex-col justify-between">
            <div>
              <div className="flex items-center gap-4">
                <BrandLogo variant="auth" />
                <div>
                  <div className="mt-1 text-xs text-white/55">NCC &amp; SIR · Australia</div>
                </div>
              </div>

              <div className="mt-20 max-w-lg space-y-6">
                <p className="text-[12px] font-semibold uppercase tracking-[0.28em] text-[#f1ddab]">Australian building &amp; electrical rules</p>
                <h2 className="text-4xl font-semibold leading-tight tracking-tight text-white sm:text-5xl">
                  Ask grounded questions over NCC and SIR.
                </h2>
                <p className="text-sm leading-7 text-slate-300">
                  Evidence-led Q&amp;A across shared National Construction Code volumes and Service &amp; Installation Rules editions.
                </p>
              </div>
            </div>

            <div className="grid gap-3 text-sm text-slate-200 sm:grid-cols-3">
              {['Clause evidence', 'Secure projects', 'Fast retrieval'].map((item) => (
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
              <p className="augusta-eyebrow mb-3">Secure sign in</p>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Welcome back</h1>
              <p className="mt-2 text-sm leading-6 text-slate-500">Access your NCC and SIR Q&amp;A workspace.</p>
            </div>

            {error && (
              <div className="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            )}

            {success && (
              <div className="mb-4 rounded-2xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
                {success}
              </div>
            )}

            <form onSubmit={handleLogin} className="space-y-4">
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
                />
              </div>
              <div className="text-left text-sm">
                <div className="flex items-center justify-between gap-2">
                  <label htmlFor="password" className="text-gray-700">
                    Password
                  </label>
                  <button
                    type="button"
                    onClick={() => navigate('/forgot-password')}
                    className="text-xs font-semibold text-[#9a7a35] hover:underline"
                  >
                    Forgot password?
                  </button>
                </div>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="augusta-input mt-1"
                  placeholder="Enter your password"
                  required
                />
              </div>
              
              {/* reCAPTCHA */}
              {RECAPTCHA_SITE_KEY && (
                <div className="flex justify-center">
                  <ReCAPTCHA
                    ref={recaptchaRef}
                    sitekey={RECAPTCHA_SITE_KEY}
                    onChange={handleCaptchaChange}
                    theme="light"
                  />
                </div>
              )}
              
              <button
                type="submit"
                disabled={loading || (RECAPTCHA_SITE_KEY ? !captchaToken : false)}
                className="augusta-button-primary mt-2 w-full justify-center py-3"
              >
                {loading ? 'Signing in...' : 'Continue'}
              </button>
            </form>

            <div className="my-6 flex items-center gap-3 text-xs text-gray-400">
              <span className="h-px flex-1 bg-gray-200" />
              or
              <span className="h-px flex-1 bg-gray-200" />
            </div>

            <button
              type="button"
              onClick={() => navigate('/signup')}
              className="augusta-button-secondary w-full justify-center py-3"
            >
              Create new account
            </button>

            <p className="mt-4 text-center text-xs text-gray-500">
              Need help?{' '}
              <a href={supportMailto()} className="font-semibold text-[#9a7a35] hover:underline">
                Contact support
              </a>
            </p>
          </div>
        </section>
      </div>
    </div>
  );
};

export default SimpleLogin;
