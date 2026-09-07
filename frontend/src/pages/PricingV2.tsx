/**
 * Pricing Page V2
 * Fetches products/prices from API and uses price_id (Reference 2 pattern)
 */
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { supabase } from '../lib/supabase';
import { BASE_API_URL } from '../lib/api';

interface Price {
  id: string;
  product_id: string;
  active: boolean;
  currency: string;
  unit_amount: number;
  interval: string | null;
  interval_count: number | null;
  type: string;
  metadata: any;
}

interface Product {
  id: string;
  name: string;
  description: string | null;
  active: boolean;
  image: string | null;
  metadata: any;
  prices: Price[];
}

const PricingV2: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedPriceId, setSelectedPriceId] = useState<string | null>(null);
  const [loadingProducts, setLoadingProducts] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProducts();
  }, []);

  const fetchProducts = async () => {
    try {
      setLoadingProducts(true);
      setError(null);

      const response = await fetch(`${BASE_API_URL}/api/v1/subscriptions/products`);
      
      if (!response.ok) {
        const errorText = await response.text();
        console.error('Error response:', errorText);
        throw new Error(`Failed to fetch products: ${response.status} ${errorText}`);
      }

      const data = await response.json();
      
      // Handle both array and object responses
      if (Array.isArray(data)) {
        setProducts(data);
      } else if (data.products) {
        setProducts(data.products);
      } else {
        setProducts([]);
      }
    } catch (err: any) {
      console.error('Error fetching products:', err);
      const errorMessage = err.message || 'Failed to load pricing. Please try again later.';
      setError(errorMessage);
    } finally {
      setLoadingProducts(false);
    }
  };

  const handleSubscribe = async (priceId: string) => {
    if (!user) {
      navigate('/login', { state: { returnUrl: '/pricing' } });
      return;
    }

    try {
      setLoading(true);
      setSelectedPriceId(priceId);

      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      
      if (!token) {
        throw new Error('Not authenticated');
      }

      const successUrl = `${window.location.origin}/subscription-success`;
      const cancelUrl = `${window.location.origin}/pricing`;

      const response = await fetch(`${BASE_API_URL}/api/v1/subscriptions/create-checkout-session`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          price_id: priceId,
          success_url: successUrl,
          cancel_url: cancelUrl,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Failed to create checkout session' }));
        throw new Error(errorData.detail || 'Failed to create checkout session');
      }

      const data = await response.json();
      
      if (data.session_url) {
        window.location.href = data.session_url;
      } else {
        throw new Error('No checkout URL received');
      }
    } catch (err: any) {
      console.error('Checkout error:', err);
      alert(err.message || 'An error occurred. Please try again.');
    } finally {
      setLoading(false);
      setSelectedPriceId(null);
    }
  };

  const formatPrice = (unitAmount: number, currency: string = 'aud') => {
    return new Intl.NumberFormat('en-AU', {
      style: 'currency',
      currency: currency.toUpperCase(),
      minimumFractionDigits: 0,
    }).format(unitAmount / 100);
  };

  const getIntervalText = (interval: string | null, intervalCount: number | null) => {
    if (!interval) return '';
    if (intervalCount && intervalCount > 1) {
      return `every ${intervalCount} ${interval}s`;
    }
    return `/${interval}`;
  };

  if (loadingProducts) {
    return (
      <div className="min-h-screen bg-gray-50 py-12 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          <p className="mt-4 text-gray-600">Loading pricing plans...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 py-12 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button
            onClick={fetchProducts}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
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
        <div className="text-center mb-16">
          <h1 className="text-4xl font-bold text-gray-900">Choose Your Plan</h1>
          <p className="mt-4 text-xl text-gray-600">
            Select the perfect plan for your professional needs
          </p>
        </div>

        {/* Pricing Cards */}
        {products.length === 0 && (
          <div className="text-center py-12">
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 max-w-2xl mx-auto">
              <h3 className="text-lg font-semibold text-yellow-900 mb-2">No Pricing Plans Available</h3>
              <p className="text-yellow-800 mb-4">
                Products haven't been synced from Stripe yet. This usually happens when:
              </p>
              <ul className="text-left text-yellow-700 text-sm space-y-2 mb-4">
                <li>• Products haven't been created in Stripe Dashboard</li>
                <li>• Webhook hasn't synced products to the database</li>
                <li>• Database migration hasn't been run</li>
              </ul>
              <p className="text-yellow-700 text-sm">
                Please contact support or check the backend logs for more information.
              </p>
              <button
                onClick={fetchProducts}
                className="mt-4 bg-yellow-600 text-white px-4 py-2 rounded-lg hover:bg-yellow-700 transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )}
        {products.length > 0 && (
          <div className="grid gap-8 lg:grid-cols-3">
            {products.map((product) => {
              // Get the first active recurring price (or first price if no recurring)
              const recurringPrices = product.prices.filter(p => p.active && p.type === 'recurring');
              const price = recurringPrices.length > 0 ? recurringPrices[0] : product.prices.find(p => p.active);
              
              if (!price) return null;

              const isPopular = product.metadata?.popular === true || product.metadata?.featured === true;
              const features = product.metadata?.features || [];
              const isFreePlan = price.unit_amount === 0;

              return (
                <div
                  key={product.id}
                  className={`relative bg-white rounded-lg shadow-lg overflow-hidden ${
                    isPopular ? 'ring-2 ring-blue-600 transform scale-105' : ''
                  }`}
                >
                  {isPopular && (
                    <div className="absolute top-0 right-0 bg-blue-600 text-white px-4 py-1 text-sm font-semibold">
                      Most Popular
                    </div>
                  )}

                  <div className="p-8">
                    {product.image && (
                      <div className="mb-4">
                        <img
                          src={product.image}
                          alt={product.name}
                          className="w-16 h-16 object-contain mx-auto"
                        />
                      </div>
                    )}

                    <h3 className="text-2xl font-semibold text-gray-900">{product.name}</h3>
                    
                    {product.description && (
                      <p className="mt-2 text-gray-600 text-sm">{product.description}</p>
                    )}

                    <div className="mt-4 flex items-baseline">
                      <span className="text-5xl font-extrabold text-gray-900">
                        {formatPrice(price.unit_amount, price.currency)}
                      </span>
                      <span className="ml-2 text-xl text-gray-500">
                        {getIntervalText(price.interval, price.interval_count)}
                      </span>
                    </div>

                    {features.length > 0 && (
                      <ul className="mt-8 space-y-4">
                        {features.map((feature: string, index: number) => (
                          <li key={index} className="flex items-start">
                            <svg
                              className="flex-shrink-0 w-6 h-6 text-green-500"
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
                            <span className="ml-3 text-gray-700">{feature}</span>
                          </li>
                        ))}
                      </ul>
                    )}

                    {!isFreePlan ? (
                      <button
                        onClick={() => handleSubscribe(price.id)}
                        disabled={loading && selectedPriceId === price.id}
                        className={`mt-8 w-full py-3 px-6 rounded-lg font-semibold transition-colors ${
                          isPopular
                            ? 'bg-blue-600 text-white hover:bg-blue-700'
                            : 'bg-gray-800 text-white hover:bg-gray-900'
                        } disabled:opacity-50 disabled:cursor-not-allowed`}
                      >
                        {loading && selectedPriceId === price.id ? 'Loading...' : 'Get Started'}
                      </button>
                    ) : (
                      <p className="mt-8 text-center text-sm text-gray-500">
                        No checkout — this tier is free and already available on your account.
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
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
                What happens if I reach my document limit?
              </h3>
              <p className="mt-2 text-gray-600">
                You can upgrade to a higher plan or wait for your subscription to renew. Company plans include unlimited documents.
              </p>
            </div>

            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-semibold text-gray-900">
                How do company subscriptions work?
              </h3>
              <p className="mt-2 text-gray-600">
                Company subscriptions allow you to invite employees who automatically get access based on your company's plan. You can manage all employees from your company dashboard.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PricingV2;

