/**
 * Stripe Subscription Details Component
 * Displays complete subscription information retrieved directly from Stripe API
 */
import React, { useState, useEffect, useRef } from 'react';
import { supabase } from '../../lib/supabase';
import { apiUrl } from '../../lib/api';

interface StripeSubscription {
  id: string;
  object: string;
  status: string;
  customer: string;
  items: any;
  current_period_start: number;
  current_period_end: number;
  cancel_at_period_end: boolean;
  canceled_at: number | null;
  cancel_at: number | null;
  created: number;
  ended_at: number | null;
  trial_start: number | null;
  trial_end: number | null;
  metadata: Record<string, any>;
  default_payment_method: string | null;
  default_source: string | null;
  latest_invoice: string | null;
  next_pending_invoice_item_invoice: number | null;
  pending_invoice_item_interval: string | null;
  pending_setup_intent: string | null;
  pending_update: Record<string, any> | null;
  schedule: string | null;
  start_date: number;
  application: string | null;
  application_fee_percent: number | null;
  billing_cycle_anchor: number;
  billing_thresholds: Record<string, any> | null;
  collection_method: string;
  currency: string;
  days_until_due: number | null;
  default_tax_rates: any[];
  description: string | null;
  discount: Record<string, any> | null;
  livemode: boolean;
  pause_collection: Record<string, any> | null;
  payment_settings: Record<string, any> | null;
  quantity: number;
  test_clock: string | null;
  transfer_data: Record<string, any> | null;
  trial_period_days: number | null;
}

/** Stripe product names may still say NCC/SIR; do not show that in the UI. */
function displayPlanName(raw: string | undefined | null): string {
  const name = (raw || '').trim();
  if (!name) return 'Plan';
  if (!/NCC|\bSIR\b|Australia/i.test(name)) return name;
  const lower = name.toLowerCase();
  if (lower.includes('company large')) return 'Company Large';
  if (lower.includes('company')) return 'Company Small';
  if (lower.includes('professional')) return 'Professional';
  if (lower.includes('sole')) return 'Sole';
  return 'Compliance library';
}

const StripeSubscriptionDetails: React.FC = () => {
  const [subscriptions, setSubscriptions] = useState<StripeSubscription[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSubscription, setExpandedSubscription] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [autoFetching, setAutoFetching] = useState(true);
  const retryTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const hasInitiallyFetched = useRef(false);

  const fetchStripeStatus = async (isRetry: boolean = false) => {
    try {
      setLoading(true);
      if (!isRetry) {
        setError(null);
      }

      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        throw new Error('Not authenticated');
      }

      // Add timeout to fetch (Stripe can be slow; avoid false "network error")
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000);

      try {
        const response = await fetch(apiUrl('/api/v1/subscriptions/stripe-status'), {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          signal: controller.signal,
        });

        clearTimeout(timeoutId);

        // 404 from this endpoint means "user has no Stripe customer/subscription yet".
        // That is a normal state; do not display it as an error banner.
        if (response.status === 404) {
          setSubscriptions([]);
          setAutoFetching(false);
          setRetryCount(0);
          setError(null);
          return;
        }

        if (!response.ok) {
          let errorText = '';
          try {
            errorText = await response.text();
          } catch {
            errorText = `HTTP ${response.status} ${response.statusText}`;
          }
          // Do not put the substring "Failed to fetch" here — the browser uses that exact
          // phrase for real network/CORS failures, and we must not confuse the two.
          throw new Error(`Stripe status HTTP ${response.status}: ${errorText || response.statusText}`);
        }

        const data = await response.json();
        setSubscriptions(data || []);
      } catch (fetchError: any) {
        clearTimeout(timeoutId);
        
        // Handle specific error types
        if (fetchError.name === 'AbortError') {
          throw new Error(
            'Request timeout: Stripe status took too long. The backend may be busy or Stripe may be slow. Try again, and check backend logs if it keeps happening.'
          );
        } else if (
          String(fetchError?.message || '').includes('Failed to fetch') ||
          String(fetchError?.message || '').includes('NetworkError when attempting to fetch resource') ||
          String(fetchError?.message || '').includes('Load failed') ||
          fetchError?.name === 'NetworkError'
        ) {
          throw new Error(
            `Network error: Cannot reach backend server at ${typeof window !== 'undefined' ? window.location.origin : ''}. Please check:\n1. Is the backend server running?\n2. Is Nginx proxying /api/ to the backend?\n3. Check browser console for blocked requests (CORS/mixed content).`
          );
        } else {
          throw fetchError;
        }
      }
      
      // Reset retry count on success (even if no subscriptions found)
      setRetryCount(0);
      setAutoFetching(false);
      
      // Clear any pending retry timeouts
      if (retryTimeoutRef.current) {
        clearTimeout(retryTimeoutRef.current);
        retryTimeoutRef.current = null;
      }
    } catch (err: any) {
      console.error('Error fetching Stripe subscription status:', err);
      let errorMessage = err.message || 'Could not load Stripe subscription status.';
      const isHttp4xx = /^Stripe status HTTP 4\d\d:/.test(errorMessage);
      const skipRetry = isHttp4xx || errorMessage === 'Not authenticated';

      if (skipRetry) {
        setAutoFetching(false);
      }

      // Keep newlines; parent uses whitespace-pre-line (do not inject <br> — it shows as literal text)
      setError(errorMessage);

      // Auto-retry with exponential backoff (max 3 retries) — not for 4xx / auth (not transient)
      if (autoFetching && !skipRetry) {
        setRetryCount(currentRetry => {
          const newRetryCount = currentRetry + 1;
          
          if (newRetryCount <= 3) {
            const delay = Math.min(1000 * Math.pow(2, currentRetry), 5000); // 1s, 2s, 4s max
            console.log(`Retrying fetch in ${delay}ms (attempt ${newRetryCount}/3)...`);
            
            retryTimeoutRef.current = setTimeout(() => {
              fetchStripeStatus(true);
            }, delay);
          } else {
            setAutoFetching(false);
            setError(`${errorMessage} (Auto-retry failed after 3 attempts. Click "Refresh from Stripe" to try again.)`);
          }
          
          return newRetryCount;
        });
      }
    } finally {
      setLoading(false);
    }
  };

  // Auto-fetch on component mount
  useEffect(() => {
    if (!hasInitiallyFetched.current) {
      hasInitiallyFetched.current = true;
      fetchStripeStatus();
    }

    // Cleanup timeout on unmount
    return () => {
      if (retryTimeoutRef.current) {
        clearTimeout(retryTimeoutRef.current);
      }
    };
  }, []);

  const formatTimestamp = (timestamp: number | null): string => {
    if (!timestamp) return 'N/A';
    return new Date(timestamp * 1000).toLocaleString();
  };

  const formatCurrency = (amount: number, currency: string = 'aud'): string => {
    return new Intl.NumberFormat('en-AU', {
      style: 'currency',
      currency: currency.toUpperCase(),
    }).format(amount / 100);
  };

  // Get subscription price based on plan type
  const getSubscriptionPrice = (subscription: StripeSubscription): string | null => {
    // Get plan name from subscription
    const planDisplayName = subscription.items?.data?.[0]?.plan_display_name;
    const planName = subscription.items?.data?.[0]?.plan_name;
    
    // Determine plan type
    let planType = '';
    if (planDisplayName) {
      planType = planDisplayName.toLowerCase();
    } else if (planName) {
      planType = planName.toLowerCase();
    } else {
      // Try to get from price ID
      const priceId = subscription.items?.data?.[0]?.price?.id;
      if (priceId === 'price_1STgi277fHiRL91ae6rpL7rK') {
        planType = 'sole trader';
      } else if (priceId === 'price_1SOpHt77fHiRL91aVZel9QOV') {
        planType = 'individual';
      } else if (priceId === 'price_1SOpIl77fHiRL91aoVoc6YQo') {
        planType = 'professional';
      }
    }
    
    // Map plan type to price
    if (planType.includes('sole trader') || planType.includes('sole_trader') || planType === 'free') {
      return '$0.00 / month';
    } else if (planType.includes('individual')) {
      return '$19.00 / month';
    } else if (planType.includes('company large')) {
      return '$299.00 / month';
    } else if (planType.includes('company')) {
      return '$199.00 / month';
    } else if (planType.includes('professional')) {
      return '$19.00 / month';
    }
    
    // Fallback: use price from Stripe if available
    if (subscription.items?.data?.[0]?.price?.unit_amount) {
      const interval = subscription.items.data[0].price.recurring?.interval || 'month';
      return `${formatCurrency(subscription.items.data[0].price.unit_amount, subscription.currency)} / ${interval}`;
    }
    
    return null;
  };

  const syncToDatabase = async () => {
    try {
      setSyncing(true);
      setSyncMessage(null);
      setError(null);

      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        throw new Error('Not authenticated');
      }

      const response = await fetch(apiUrl('/api/v1/subscriptions/sync-to-supabase'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Failed to sync to database: ${response.status} ${errorText}`);
      }

      const data = await response.json();
      if (data.success) {
        setSyncMessage(data.message);
      } else {
        setSyncMessage(data.message);
      }

      setTimeout(() => {
        setAutoFetching(true);
        setRetryCount(0);
        setError(null);
        if (retryTimeoutRef.current) {
          clearTimeout(retryTimeoutRef.current);
          retryTimeoutRef.current = null;
        }
        fetchStripeStatus();
      }, 1000);
    } catch (err: any) {
      console.error('Error syncing to database:', err);
      setError(err.message || 'Failed to sync subscriptions to database');
    } finally {
      setSyncing(false);
    }
  };

const getStatusColor = (status: string): string => {
    switch (status) {
      case 'active':
        return 'text-green-600 bg-green-50';
      case 'trialing':
        return 'text-blue-600 bg-blue-50';
      case 'past_due':
        return 'text-yellow-600 bg-yellow-50';
      case 'canceled':
      case 'unpaid':
        return 'text-red-600 bg-red-50';
      default:
        return 'text-gray-600 bg-gray-50';
    }
  };

  return (
    <div className="bg-white rounded-lg shadow-lg p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Billing status</h2>
          <p className="text-sm text-gray-600 mt-1">
            Your current plan and billing period
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={syncToDatabase}
            disabled={syncing || subscriptions.length === 0}
            className="augusta-button-secondary disabled:cursor-not-allowed disabled:opacity-60"
            title="Sync subscription data from Stripe to database"
          >
            {syncing ? 'Syncing...' : 'Sync to Database'}
          </button>
          <button
            type="button"
            onClick={() => {
              setAutoFetching(true);
              setRetryCount(0);
              setError(null);
              if (retryTimeoutRef.current) {
                clearTimeout(retryTimeoutRef.current);
                retryTimeoutRef.current = null;
              }
              fetchStripeStatus();
            }}
            disabled={loading}
            className="augusta-button-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? 'Loading...' : 'Refresh from Stripe'}
          </button>
        </div>
      </div>

      {syncMessage && (
        <div className="mb-4 rounded-2xl border border-slate-200/80 bg-[#fffaf1] p-4 text-sm text-slate-800">
          <p>{syncMessage}</p>
        </div>
      )}

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
          <p className="text-red-800 font-semibold mb-2">Error:</p>
          <p className="text-red-800 whitespace-pre-line">{error}</p>
          <div className="mt-3 text-sm text-red-700">
            <p>
              <strong>Request URL:</strong>{' '}
              {typeof window !== 'undefined'
                ? (() => {
                    const u = apiUrl('/api/v1/subscriptions/stripe-status');
                    return u.startsWith('http') ? u : `${window.location.origin}${u}`;
                  })()
                : ''}
            </p>
            {typeof window !== 'undefined' &&
            (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') ? (
              <>
                <p className="mt-2">Local dev checklist:</p>
                <ul className="list-disc list-inside mt-1 space-y-1">
                  <li>
                    Backend running: <code className="text-xs">uvicorn main:app --reload --port 8000</code>
                  </li>
                  <li>
                    CRA proxy in <code className="text-xs">package.json</code> must match that port (8000)
                  </li>
                  <li>
                    Re-run <code className="text-xs">supabase/combined_setup.sql</code> in the database SQL editor if logs
                    mention missing <code className="text-xs">profiles.stripe_customer_id</code>
                  </li>
                </ul>
              </>
            ) : (
              <>
                <p className="mt-2 text-xs text-red-600">
                  Production: use <code className="bg-red-100 px-1">augustasearch.com</code> (not an old domain).
                </p>
                <p className="mt-2">Try checking:</p>
                <ul className="list-disc list-inside mt-1 space-y-1">
                  <li>Nginx proxies <code className="text-xs">/api/</code> to the backend port</li>
                  <li>Browser Network tab for failed requests</li>
                </ul>
              </>
            )}
          </div>
        </div>
      )}

      {subscriptions.length === 0 && !loading && !autoFetching && (
        <div className="text-center py-8">
          <p className="text-gray-600 mb-2">No active subscription yet</p>
          <p className="text-sm text-gray-500 mb-4">
            If you haven&apos;t subscribed, this is normal. Click <strong>Upgrade Plan</strong> above to start a plan, then come back and refresh.
          </p>
          <button
            type="button"
            onClick={() => {
              setAutoFetching(true);
              setRetryCount(0);
              fetchStripeStatus();
            }}
            className="augusta-button-secondary"
          >
            Refresh from Stripe
          </button>
        </div>
      )}

      {loading && subscriptions.length === 0 && (
        <div className="text-center py-8">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mb-4"></div>
          <p className="text-gray-600">
            {autoFetching && retryCount > 0 
              ? `Loading billing status... (Retry ${retryCount}/3)` 
              : 'Loading billing status...'}
          </p>
        </div>
      )}

      {subscriptions.length > 0 && (
        <div className="space-y-4">
          {subscriptions.map((sub) => (
            <div
              key={sub.id}
              className="border border-gray-200 rounded-lg overflow-hidden"
            >
              <div
                className="p-4 bg-gray-50 cursor-pointer hover:bg-gray-100"
                onClick={() => setExpandedSubscription(
                  expandedSubscription === sub.id ? null : sub.id
                )}
              >
                <div className="flex justify-between items-center">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      {sub.items?.data?.[0]?.plan_display_name && (
                        <span className="font-bold text-lg text-blue-900">
                          {displayPlanName(sub.items.data[0].plan_display_name)}
                        </span>
                      )}
                      {!sub.items?.data?.[0]?.plan_display_name && sub.items?.data?.[0]?.price?.product && (
                        <span className="font-bold text-lg text-blue-900">
                          {typeof sub.items.data[0].price.product === 'string'
                            ? 'Professional'
                            : displayPlanName(sub.items.data[0].price.product.name)}
                        </span>
                      )}
                      <span className={`px-2 py-1 rounded text-xs font-medium ${getStatusColor(sub.status)}`}>
                        {sub.status.toUpperCase()}
                      </span>
                      {!sub.livemode && (
                        <span className="px-2 py-1 rounded text-xs font-medium bg-yellow-100 text-yellow-800">
                          TEST
                        </span>
                      )}
                    </div>
                    {getSubscriptionPrice(sub) && (
                      <p className="text-sm text-gray-600 mt-1">
                        {getSubscriptionPrice(sub)}
                      </p>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-sm text-gray-600">
                      Period: {formatTimestamp(sub.current_period_start)} → {formatTimestamp(sub.current_period_end)}
                    </p>
                    {sub.cancel_at_period_end && (
                      <p className="text-sm text-orange-600 mt-1">
                        Cancels at period end
                      </p>
                    )}
                  </div>
                </div>
              </div>

              {expandedSubscription === sub.id && (
                <div className="p-6 bg-white border-t border-gray-200">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {(sub.cancel_at_period_end || sub.canceled_at || sub.cancel_at) && (
                      <div>
                        <h3 className="font-semibold text-gray-900 mb-3">Cancellation</h3>
                        <dl className="space-y-2 text-sm">
                          {sub.cancel_at_period_end && (
                            <div>
                              <dt className="text-gray-600">Cancel at period end</dt>
                              <dd className="text-orange-600">Yes</dd>
                            </div>
                          )}
                          {sub.canceled_at && (
                            <div>
                              <dt className="text-gray-600">Canceled at</dt>
                              <dd className="text-gray-900">{formatTimestamp(sub.canceled_at)}</dd>
                            </div>
                          )}
                          {sub.cancel_at && (
                            <div>
                              <dt className="text-gray-600">Will cancel at</dt>
                              <dd className="text-gray-900">{formatTimestamp(sub.cancel_at)}</dd>
                            </div>
                          )}
                        </dl>
                      </div>
                    )}

                    {(sub.trial_start || sub.trial_end) && (
                      <div>
                        <h3 className="font-semibold text-gray-900 mb-3">Trial</h3>
                        <dl className="space-y-2 text-sm">
                          {sub.trial_start && (
                            <div>
                              <dt className="text-gray-600">Trial start</dt>
                              <dd className="text-gray-900">{formatTimestamp(sub.trial_start)}</dd>
                            </div>
                          )}
                          {sub.trial_end && (
                            <div>
                              <dt className="text-gray-600">Trial end</dt>
                              <dd className="text-gray-900">{formatTimestamp(sub.trial_end)}</dd>
                            </div>
                          )}
                        </dl>
                      </div>
                    )}

                    {sub.items && sub.items.data && sub.items.data.length > 0 && (
                      <div>
                        <h3 className="font-semibold text-gray-900 mb-3">Plan</h3>
                        <div className="space-y-3">
                          {sub.items.data.map((item: any, index: number) => {
                            const subscriptionType = displayPlanName(
                              item.plan_display_name ||
                                (item.price?.product && typeof item.price.product === 'object'
                                  ? item.price.product.name
                                  : 'Professional')
                            );

                            return (
                              <div key={index} className="border border-gray-200 rounded p-3">
                                <p className="text-base text-gray-900 font-bold mb-2">
                                  {subscriptionType}
                                </p>
                                {item.price?.unit_amount && (
                                  <p className="text-sm text-gray-600">
                                    Amount: {formatCurrency(item.price.unit_amount, sub.currency)}
                                    {item.price.recurring && ` / ${item.price.recurring.interval}`}
                                  </p>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    <div>
                      <h3 className="font-semibold text-gray-900 mb-3">Payment</h3>
                      <dl className="space-y-2 text-sm">
                        {getSubscriptionPrice(sub) && (
                          <div>
                            <dt className="text-gray-600">Amount</dt>
                            <dd className="text-gray-900 font-semibold">
                              {getSubscriptionPrice(sub)}
                            </dd>
                          </div>
                        )}
                        {sub.current_period_start && (
                          <div>
                            <dt className="text-gray-600">Current period started</dt>
                            <dd className="text-gray-900">{formatTimestamp(sub.current_period_start)}</dd>
                          </div>
                        )}
                      </dl>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default StripeSubscriptionDetails;

