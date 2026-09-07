/**
 * Subscription Status Component
 * Displays current subscription info and usage limits
 */
import React, { useState } from 'react';
import { useSubscription } from '../../hooks/useSubscription';
import { useNavigate } from 'react-router-dom';
import StripeSubscriptionDetails from './StripeSubscriptionDetails';

const SubscriptionStatus: React.FC = () => {
  const { subscription, usageStats, loading, getDocumentsRemaining, getQuestionsRemaining } = useSubscription();
  const navigate = useNavigate();
  const [showStripeDetails, setShowStripeDetails] = useState(false);

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <p className="text-gray-600">Loading subscription...</p>
      </div>
    );
  }

  if (!usageStats || !usageStats.has_access) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">No Active Subscription</h2>
        <p className="text-gray-600 mb-4">
          Subscribe to unlock all features and start using Augusta Search.
        </p>
        <button
          onClick={() => navigate('/pricing')}
          className="bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 transition-colors"
        >
          View Plans
        </button>
      </div>
    );
  }

  const documentsRemaining = getDocumentsRemaining();
  const questionsRemaining = getQuestionsRemaining();

  const getAccountTypeLabel = () => {
    if (usageStats.subscription_source === 'passcode' && usageStats.account_type === 'professional') {
      return 'Professional (trial)';
    }
    switch (usageStats.account_type) {
      case 'trial':
        return 'Trial Access';
      case 'individual':
        return 'Individual Subscription';
      case 'professional':
        return 'Professional Subscription';
      default:
        return 'Free Account (Sole Trader)';
    }
  };

  const getStatusColor = () => {
    switch (usageStats.subscription_status) {
      case 'active':
        return 'text-green-600';
      case 'trial':
        return 'text-blue-600';
      case 'past_due':
        return 'text-yellow-600';
      case 'cancelled':
      case 'expired':
        return 'text-red-600';
      default:
        return 'text-gray-600';
    }
  };

  const planLabel = usageStats.subscription_source === 'passcode' && usageStats.account_type === 'professional'
    ? 'Professional (trial)'
    : (usageStats.plan_display_name || getAccountTypeLabel());
  const onFreePlan = usageStats.plan_name === 'sole_trader_free';
  const isPasscodeTrial = usageStats.subscription_source === 'passcode' && usageStats.account_type === 'professional';

  return (
    <div className="space-y-6">
      {/* Main Status Card */}
      <div className="bg-white rounded-lg shadow-lg p-6">
        <div className="flex justify-between items-start mb-6">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">{planLabel}</h2>
            {isPasscodeTrial && usageStats.access_expires_at && (
              <p className="text-sm text-gray-600 mt-1">
                Access until {new Date(usageStats.access_expires_at).toLocaleDateString()}
              </p>
            )}
            <p className={`text-sm font-semibold mt-1 ${getStatusColor()}`}>
              Status: {usageStats.subscription_status?.toUpperCase()}
            </p>
            {/* Stripe Status Display (for debugging) */}
            {subscription?.stripe_status && (
              <div className="mt-2 p-2 bg-blue-50 border border-blue-200 rounded text-xs">
                <p className="font-semibold text-blue-900">Stripe Status (Real-time from Stripe API):</p>
                <p className="text-blue-800">
                  <strong>Stripe Subscription ID:</strong> {subscription.stripe_subscription_id || subscription.id}
                  <br />
                  <strong>Database ID:</strong> {subscription.id && !subscription.from_stripe_only ? subscription.id : 'Not in database yet'}
                  <br />
                  <strong>Status:</strong> {subscription.stripe_status.toUpperCase()}
                  {subscription.stripe_current_period_end && (
                    <>
                      <br />
                      <strong>Period End:</strong> {new Date(subscription.stripe_current_period_end).toLocaleString()}
                    </>
                  )}
                  {subscription.stripe_current_period_start && (
                    <>
                      <br />
                      <strong>Period Start:</strong> {new Date(subscription.stripe_current_period_start).toLocaleString()}
                    </>
                  )}
                  {subscription.stripe_cancel_at_period_end && (
                    <>
                      <br />
                      <span className="text-orange-600">⚠️ Cancels at period end</span>
                    </>
                  )}
                  {subscription.stripe_trial_end && (
                    <>
                      <br />
                      <strong>Trial End:</strong> {new Date(subscription.stripe_trial_end).toLocaleString()}
                    </>
                  )}
                  {subscription.stripe_error && (
                    <>
                      <br />
                      <span className="text-red-600">❌ Error: {subscription.stripe_error}</span>
                    </>
                  )}
                  {subscription.from_stripe_only && (
                    <>
                      <br />
                      <span className="text-yellow-700">⚠️ Subscription exists in Stripe but not yet saved to database (webhook pending)</span>
                    </>
                  )}
                </p>
              </div>
            )}
            {subscription && !subscription.stripe_status && subscription.status === 'active' && (
              <div className="mt-2 p-2 bg-yellow-50 border border-yellow-200 rounded text-xs">
                <p className="text-yellow-800">
                  ⚠️ No Stripe subscription ID found. Subscription may not be synced with Stripe.
                </p>
              </div>
            )}
          </div>
        </div>

        {onFreePlan && (
          <div className="mb-6 rounded-lg border border-green-200 bg-green-50 p-4 text-sm text-green-800">
            You are currently on the Sole Trader plan. Upgrade anytime to unlock more uploads, questions, and team features.
          </div>
        )}

        {/* Expiration Notice */}
        {usageStats.access_expires_at && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
            <p className="text-sm text-blue-800">
              <strong>Access expires:</strong> {new Date(usageStats.access_expires_at).toLocaleDateString()}
            </p>
          </div>
        )}

        {/* Usage Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Documents */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <span className="text-sm font-medium text-gray-700">Documents</span>
              <span className="text-sm text-gray-600">
                {documentsRemaining === null 
                  ? 'Unlimited' 
                  : `${documentsRemaining} remaining`}
              </span>
            </div>
            
            {usageStats.max_documents !== null && (
              <>
                <div className="w-full bg-gray-200 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full ${
                      usageStats.documents_uploaded / usageStats.max_documents > 0.8
                        ? 'bg-red-600'
                        : 'bg-blue-600'
                    }`}
                    style={{
                      width: `${Math.min(100, (usageStats.documents_uploaded / usageStats.max_documents) * 100)}%`,
                    }}
                  />
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  {usageStats.documents_uploaded} / {usageStats.max_documents} used
                </p>
              </>
            )}
          </div>

          {/* Questions */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <span className="text-sm font-medium text-gray-700">Questions</span>
              <span className="text-sm text-gray-600">
                {questionsRemaining === null 
                  ? 'Unlimited' 
                  : `${questionsRemaining} remaining`}
              </span>
            </div>
            
            {usageStats.max_questions !== null && (
              <>
                <div className="w-full bg-gray-200 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full ${
                      usageStats.questions_asked / usageStats.max_questions > 0.8
                        ? 'bg-red-600'
                        : 'bg-green-600'
                    }`}
                    style={{
                      width: `${Math.min(100, (usageStats.questions_asked / usageStats.max_questions) * 100)}%`,
                    }}
                  />
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  {usageStats.questions_asked} / {usageStats.max_questions} asked
                </p>
              </>
            )}
          </div>
        </div>

        {/* Upgrade Notice */}
        {(documentsRemaining !== null && documentsRemaining < 2) || (questionsRemaining !== null && questionsRemaining < 5) ? (
          <div className="mt-6 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm text-yellow-800 mb-2">
              <strong>Running low on quota!</strong>
            </p>
            <p className="text-xs text-yellow-700 mb-3">
              Consider upgrading to a plan with higher limits or unlimited access.
            </p>
            <button
              onClick={() => navigate('/pricing')}
              className="bg-yellow-600 text-white px-4 py-2 rounded-lg hover:bg-yellow-700 transition-colors text-sm"
            >
              Upgrade Now
            </button>
          </div>
        ) : null}
      </div>

      {/* Features Access */}
      <div className="bg-white rounded-lg shadow p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Your Features</h3>
        <div className="space-y-2">
          {['trial', 'individual', 'professional', 'free'].includes(usageStats.account_type) && (
            <>
              <div className="flex items-center text-sm text-gray-700">
                <svg className="w-5 h-5 text-green-500 mr-2" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                Document Upload
              </div>
              <div className="flex items-center text-sm text-gray-700">
                <svg className="w-5 h-5 text-green-500 mr-2" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                AI Q&A
              </div>
            </>
          )}
          
          {['individual', 'professional'].includes(usageStats.account_type) && (
            <>
              <div className="flex items-center text-sm text-gray-700">
                <svg className="w-5 h-5 text-green-500 mr-2" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                Advanced Search
              </div>
              <div className="flex items-center text-sm text-gray-700">
                <svg className="w-5 h-5 text-green-500 mr-2" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                Export Results
              </div>
              <div className="flex items-center text-sm text-gray-700">
                <svg className="w-5 h-5 text-green-500 mr-2" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                Priority Support
              </div>
            </>
          )}
        </div>
      </div>

      {/* Stripe Subscription Details – only for Stripe-paid subscriptions */}
      {usageStats.subscription_source !== 'passcode' && (
        <>
          <div className="mt-6">
            <button
              onClick={() => setShowStripeDetails(!showStripeDetails)}
              className="w-full px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors flex items-center justify-center gap-2"
            >
              <svg
                className={`w-5 h-5 transition-transform ${showStripeDetails ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
              {showStripeDetails ? 'Hide' : 'Show'} Stripe Subscription Details
            </button>
          </div>
          {showStripeDetails && (
            <div className="mt-6">
              <StripeSubscriptionDetails />
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default SubscriptionStatus;

