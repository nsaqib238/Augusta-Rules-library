/**
 * useSubscription Hook
 * Manages user subscription state and operations
 */
import { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { supabase } from '../lib/supabase';
import { apiUrl } from '../lib/api';

export interface Subscription {
  id: string;
  subscription_type: string;
  plan_name: string;
  status: string;
  current_period_end: string;
  max_documents: number | null;
  max_questions: number | null;
  documents_uploaded: number;
  questions_asked: number;
  // Stripe status fields (for debugging)
  stripe_subscription_id?: string | null;  // Stripe subscription ID (sub_xxxxx)
  stripe_status?: string | null;
  stripe_current_period_start?: string | null;
  stripe_current_period_end?: string | null;
  stripe_cancel_at_period_end?: boolean | null;
  stripe_canceled_at?: string | null;
  stripe_trial_end?: string | null;
  stripe_error?: string | null;
  from_stripe_only?: boolean | null;  // True if subscription exists in Stripe but not in database yet
}

export interface UsageStats {
  has_access: boolean;
  account_type: string;
  plan_name?: string | null;
  plan_display_name?: string | null;
  max_documents: number | null;
  max_questions: number | null;
  documents_uploaded: number;
  questions_asked: number;
  access_expires_at: string | null;
  subscription_status: string | null;
  subscription_source?: string | null; // 'stripe' | 'passcode' for display
}

export const useSubscription = () => {
  const { user } = useAuth();
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch subscription and usage stats
  useEffect(() => {
    if (!user) {
      setSubscription(null);
      setUsageStats(null);
      setLoading(false);
      return;
    }

    fetchSubscriptionData();
  }, [user]);

  const fetchSubscriptionData = async () => {
    try {
      setLoading(true);
      setError(null);

      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        setLoading(false);
        return;
      }

      // Fetch subscription with timeout
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout
        
        const subResponse = await fetch(apiUrl('/api/v1/subscriptions/my-subscription'), {
          headers: {
            'Authorization': `Bearer ${token}`,
          },
          signal: controller.signal,
        });
        
        clearTimeout(timeoutId);

        if (subResponse.ok) {
          const subData = await subResponse.json();
          console.log('🔍 Subscription Response:', subData);
          if (subData) {
            console.log('✅ Subscription found:', {
              id: subData.id,
              stripe_subscription_id: subData.stripe_subscription_id,
              status: subData.status,
              stripe_status: subData.stripe_status,
              from_stripe_only: subData.from_stripe_only,
              plan_name: subData.plan_name
            });
            setSubscription(subData);
          } else {
            console.log('⚠️ No subscription found (null response)');
            setSubscription(null);
          }
        } else {
          const errorText = await subResponse.text();
          console.error('❌ Failed to fetch subscription:', subResponse.status, errorText);
          setSubscription(null);
        }
      } catch (fetchError: any) {
        if (fetchError.name === 'AbortError') {
          console.error('❌ Subscription request timeout (30s)');
          setError('Subscription request timed out. The backend may be slow or Stripe API is taking too long.');
        } else {
          console.error('❌ Network error fetching subscription:', fetchError);
          setError('Failed to fetch subscription. Check if backend is running or CORS is configured correctly.');
        }
        setSubscription(null);
      }

      // Fetch usage stats (separate try — avoids outer catch if subscription fetch already failed)
      try {
        const statsResponse = await fetch(apiUrl('/api/v1/subscriptions/usage-stats'), {
          headers: {
            'Authorization': `Bearer ${token}`,
          },
        });

        if (statsResponse.ok) {
          const statsData = await statsResponse.json();
          console.log('🔍 Usage Stats Response:', statsData);
          console.log('🔍 Account Type:', statsData.account_type);
          setUsageStats(statsData);
        } else {
          console.error('❌ Failed to fetch usage stats:', statsResponse.status, await statsResponse.text());
        }
      } catch (statsErr) {
        console.error('❌ Network error fetching usage stats:', statsErr);
      }
    } catch (err) {
      console.error('Error fetching subscription data:', err);
      setError('Failed to load subscription data');
    } finally {
      setLoading(false);
    }
  };

  const createCheckoutSession = async (
    planName: string,
    successUrl: string,
    cancelUrl: string,
    options?: { referralCode?: string; companyName?: string }
  ): Promise<string | null> => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        throw new Error('Not authenticated');
      }

      const body: Record<string, string> = {
        plan_name: planName,
        success_url: successUrl,
        cancel_url: cancelUrl,
      };
      if (options?.referralCode?.trim()) {
        body.referral_code = options.referralCode.trim();
      }
      if (options?.companyName?.trim()) {
        body.company_name = options.companyName.trim();
      }

      const response = await fetch(apiUrl('/api/v1/subscriptions/create-checkout-session'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const errBody = await response.json().catch(() => ({}));
        throw new Error(errBody.detail || 'Failed to create checkout session');
      }

      const data = await response.json();
      return data.session_url;
    } catch (err) {
      console.error('Error creating checkout session:', err);
      setError(err instanceof Error ? err.message : 'Failed to start checkout');
      return null;
    }
  };

  const joinCompany = async (code: string): Promise<{ success: boolean; message: string }> => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        throw new Error('Not authenticated');
      }
      const response = await fetch(apiUrl('/api/v1/subscriptions/join-company'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ code: code.trim() }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || 'Invalid company code');
      }
      await fetchSubscriptionData();
      return { success: true, message: data.message || 'Joined company.' };
    } catch (err: any) {
      return { success: false, message: err?.message || 'Failed to join company' };
    }
  };

  const redeemPasscode = async (code: string): Promise<{ success: boolean; message: string; access_expires_at?: string }> => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        throw new Error('Not authenticated');
      }
      const response = await fetch(apiUrl('/api/v1/subscriptions/redeem-passcode'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ code: code.trim() }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || 'Invalid or expired passcode');
      }
      await fetchSubscriptionData();
      return { success: true, message: data.message, access_expires_at: data.access_expires_at };
    } catch (err: any) {
      const message = err?.message || 'Failed to redeem passcode';
      return { success: false, message };
    }
  };

  const createBillingPortalSession = async (returnUrl: string): Promise<string | null> => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        throw new Error('Not authenticated');
      }

      const response = await fetch(apiUrl('/api/v1/subscriptions/create-portal-session'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ return_url: returnUrl }),
      });

      if (!response.ok) {
        throw new Error('Failed to create portal session');
      }

      const data = await response.json();
      return data.portal_url;
    } catch (err) {
      console.error('Error creating portal session:', err);
      setError('Failed to open billing portal');
      return null;
    }
  };

  const canUploadDocument = (): boolean => {
    if (!usageStats) return false;
    
    if (usageStats.max_documents === null) return true; // Unlimited
    
    return usageStats.documents_uploaded < usageStats.max_documents;
  };

  const canAskQuestion = (): boolean => {
    if (!usageStats) return false;
    
    if (usageStats.max_questions === null) return true; // Unlimited
    
    return usageStats.questions_asked < usageStats.max_questions;
  };

  const getDocumentsRemaining = (): number | null => {
    if (!usageStats) return 0;
    if (usageStats.max_documents === null) return null; // Unlimited
    
    return Math.max(0, usageStats.max_documents - usageStats.documents_uploaded);
  };

  const getQuestionsRemaining = (): number | null => {
    if (!usageStats) return 0;
    if (usageStats.max_questions === null) return null; // Unlimited
    
    return Math.max(0, usageStats.max_questions - usageStats.questions_asked);
  };

  return {
    subscription,
    usageStats,
    loading,
    error,
    createCheckoutSession,
    createBillingPortalSession,
    redeemPasscode,
    joinCompany,
    canUploadDocument,
    canAskQuestion,
    getDocumentsRemaining,
    getQuestionsRemaining,
    refreshData: fetchSubscriptionData,
  };
};

