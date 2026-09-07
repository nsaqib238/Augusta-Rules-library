/**
 * Pricing Page with Stripe Pricing Table
 * Uses Stripe's built-in pricing table component OR custom checkout flow
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

// Get from environment variables or use defaults
// Update these in your .env file:
// REACT_APP_STRIPE_PRICING_TABLE_ID=prctbl_1STgmd77fHiRL91aijHLxXWA
// REACT_APP_STRIPE_PUBLISHABLE_KEY=pk_test_...
const STRIPE_PRICING_TABLE_ID = process.env.REACT_APP_STRIPE_PRICING_TABLE_ID || 'prctbl_1STgmd77fHiRL91aijHLxXWA';
const STRIPE_PUBLISHABLE_KEY = process.env.REACT_APP_STRIPE_PUBLISHABLE_KEY || 'pk_test_51552T477fHiRL91aWy8mmS1AjDQyUNCTIRYOqyxsi0UB2xuCKK5fDrYKPybRh2DExY5mckbkhHeUoR1NmSAXMKiI00ehWY82yF';

const PricingStripe: React.FC = () => {
  const navigate = useNavigate();
  const [useCustomCheckout, setUseCustomCheckout] = useState(false);
  const [scriptLoaded, setScriptLoaded] = useState(false);

  useEffect(() => {
    // Load Stripe pricing table script
    const script = document.createElement('script');
    script.src = 'https://js.stripe.com/v3/pricing-table.js';
    script.async = true;
    script.onload = () => {
      setScriptLoaded(true);
    };
    script.onerror = () => {
      console.error('Failed to load Stripe pricing table script');
      setUseCustomCheckout(true);
    };
    document.body.appendChild(script);

    return () => {
      // Cleanup: remove script on unmount
      const existingScript = document.querySelector('script[src="https://js.stripe.com/v3/pricing-table.js"]');
      if (existingScript && existingScript.parentNode) {
        existingScript.parentNode.removeChild(existingScript);
      }
    };
  }, []);

  // Fallback to custom checkout if pricing table fails
  if (useCustomCheckout || !STRIPE_PRICING_TABLE_ID) {
    // Redirect to custom pricing page with checkout flow
    navigate('/pricing-old');
    return null;
  }

  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mb-6">
          <button
            onClick={() => navigate(-1)}
            className="inline-flex items-center text-sm font-medium text-gray-600 hover:text-gray-900"
          >
            <span className="mr-2">←</span>
            Back
          </button>
        </div>

        {/* Header */}
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold text-gray-900">Choose Your Plan</h1>
          <p className="mt-4 text-xl text-gray-600">
            Select the perfect plan for your professional needs
          </p>
        </div>

        {/* Stripe Pricing Table */}
        {scriptLoaded && STRIPE_PRICING_TABLE_ID && (
          <div className="flex justify-center">
            <stripe-pricing-table
              pricing-table-id={STRIPE_PRICING_TABLE_ID}
              publishable-key={STRIPE_PUBLISHABLE_KEY}
            />
          </div>
        )}
        
        {!scriptLoaded && (
          <div className="flex justify-center items-center min-h-[400px]">
            <div className="text-center">
              <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mb-4"></div>
              <p className="text-gray-600">Loading pricing plans...</p>
            </div>
          </div>
        )}

        {/* FAQ Section */}
        <div className="mt-20">
          <h2 className="text-3xl font-bold text-center text-gray-900">
            Frequently Asked Questions
          </h2>
          
          <div className="mt-10 max-w-3xl mx-auto space-y-6">
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-semibold text-gray-900">
                Can I switch plans later?
              </h3>
              <p className="mt-2 text-gray-600">
                Yes! You can upgrade or downgrade your plan at any time through your account settings.
              </p>
            </div>

            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-semibold text-gray-900">
                What happens if I reach my active standard limit?
              </h3>
              <p className="mt-2 text-gray-600">
                You can upgrade to a higher plan to increase your active standard limit. Professional plans include unlimited active standards.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PricingStripe;

