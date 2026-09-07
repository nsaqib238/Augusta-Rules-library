import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import SimpleLogin from './pages/SimpleLogin';
import SignupPage from './pages/SignupPage';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import Dashboard from './pages/Dashboard';
import Pricing from './pages/Pricing';
import PricingV2 from './pages/PricingV2';
import PricingStripe from './pages/PricingStripe';
import ProtectedRoute from './components/auth/ProtectedRoute';
import AboutApp from './pages/AboutApp';

function App() {
  return (
    <AuthProvider>
      <Router>
        <Routes>
          <Route path="/login" element={<SimpleLogin />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/pricing" element={<Pricing />} />
          <Route path="/pricing-stripe" element={<PricingStripe />} />
          <Route path="/pricing-custom" element={<PricingV2 />} />
          <Route path="/subscription-success" element={<SubscriptionSuccessPage />} />
          <Route path="/about-app" element={<AboutApp />} />
          {/* Old dedicated redeem page — redirect to pricing passcode section */}
          <Route path="/redeem-code" element={<Navigate to="/pricing#passcode" replace />} />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <Dashboard />
              </ProtectedRoute>
            }
          />
          <Route path="/" element={<Navigate to="/login" replace />} />
        </Routes>
      </Router>
    </AuthProvider>
  );
}

const SubscriptionSuccessPage: React.FC = () => {
  return (
    <div className="augusta-page-shell flex min-h-screen items-center justify-center px-4">
      <div className="augusta-card w-full max-w-md p-8 text-center">
        <div className="mb-4">
          <svg className="mx-auto h-16 w-16 text-[#9a7a35]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <h2 className="mb-2 text-2xl font-semibold text-slate-950">Augusta Search Activated</h2>
        <p className="mb-6 text-slate-600">
          Your subscription has been successfully activated. You can now access your premium standards workspace.
        </p>
        <a href="/dashboard" className="augusta-button-primary inline-block px-6 py-3">
          Go to Dashboard
        </a>
      </div>
    </div>
  );
};

export default App;
