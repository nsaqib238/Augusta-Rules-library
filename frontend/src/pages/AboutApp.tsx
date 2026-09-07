import React from 'react';

/**
 * About / Product Overview — shared compliance-library Q&A (no PDF upload / discipline tabs).
 */
const AboutApp: React.FC = () => {
  return (
    <div className="augusta-page-shell min-h-screen px-4 py-16 sm:px-6 lg:px-8">
      <div className="augusta-card mx-auto max-w-4xl space-y-8 p-8 sm:p-12">
        <div className="space-y-3">
          <p className="augusta-eyebrow">Overview</p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Augusta Search — compliance library</h1>
          <p className="text-base text-slate-600 leading-relaxed">
            <strong>Augusta Search</strong> provides evidence-led Q&amp;A over shared codes, standards, and installation
            rules. Answers use retrieval-augmented AI and are a <strong>research aid only</strong>—not a substitute for
            official publications or professional advice.
          </p>
        </div>

        <section
          className="space-y-2 rounded-2xl border border-[#f1ddab]/80 bg-[#fffaf1]/90 p-5 text-sm text-slate-800"
          aria-labelledby="disclaimer-heading"
        >
          <h2 id="disclaimer-heading" className="text-base font-semibold text-[#7c5f1e]">
            Important — please read
          </h2>
          <ul className="list-inside list-disc space-y-1.5 text-slate-800">
            <li>
              <strong>Not advice.</strong> Nothing on this page or in the app is legal, engineering, or compliance
              advice. You remain responsible for designs, certifications, and compliance with applicable laws and
              standards.
            </li>
            <li>
              <strong>No affiliation.</strong> Augusta Search is not affiliated with, endorsed by, or an official
              publisher of any standards body, regulator, or code authority.
            </li>
            <li>
              <strong>Official sources rule.</strong> Always check requirements against the current official publications
              and amendments for your project, jurisdiction, and document edition.
            </li>
            <li>
              <strong>AI limitations.</strong> Responses can be incomplete, outdated, or wrong. Do not rely on the app
              for safety-critical or contractual decisions without independent verification.
            </li>
            <li>
              <strong>Shared libraries only.</strong> This product searches admin-ingested compliance libraries.
            </li>
            <li>
              <strong>Availability.</strong> Features, content coverage, and response times depend on your plan and
              operations—the app does not promise uninterrupted access or fixed turnaround times.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-slate-950">What you can do</h2>
          <ul className="space-y-2 text-slate-700 list-disc list-inside leading-relaxed">
            <li>
              <strong>Library Q&amp;A.</strong> Ask questions against indexed documents with cited clause references.
            </li>
            <li>
              <strong>Country coverage.</strong> Open a country, pick a document type, then ask against one document.
            </li>
            <li>
              <strong>Billing.</strong> Sole includes library Q&amp;A. Professional and Company plans are paid via Stripe
              on the Pricing page (higher limits / team seats). Admins may issue promotional passcodes.
            </li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-slate-950">How it works (high level)</h2>
          <ol className="space-y-2 text-slate-700 list-decimal list-inside leading-relaxed">
            <li>
              <span className="font-semibold">Sign up.</span> New accounts start on <strong>Sole</strong> with compliance
              Q&amp;A. Upgrade on Pricing, join with a company code, or redeem a passcode if you have one.
            </li>
            <li>
              <span className="font-semibold">Choose a document.</span> Pick a country and document type, then select one
              edition from the shared library.
            </li>
            <li>
              <span className="font-semibold">Search and answer.</span> The system retrieves relevant clauses/tables and
              drafts a reply with references where available.
            </li>
          </ol>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-slate-950">Plans (summary)</h2>
          <p className="text-sm text-slate-600 leading-relaxed">
            Prices below match the current Pricing page (AUD / month). Packaging can change;{' '}
            <a href="/pricing" className="font-semibold text-[#9a7a35] hover:underline">
              Pricing
            </a>{' '}
            remains the source of truth for checkout.
          </p>
          <div className="grid sm:grid-cols-2 gap-4">
            {[
              {
                name: 'Sole',
                price: 'Free',
                features: ['Library Q&A', 'Cited answers', 'Country libraries'],
                description: 'Free access to shared compliance libraries.',
              },
              {
                name: 'Professional',
                price: '$19 / month',
                features: ['Full library Q&A', 'Higher usage limits', 'Priority support'],
                description: 'For practitioners who need more capacity on the same product.',
              },
              {
                name: 'Company Small',
                price: '$199 / month',
                features: [
                  'Up to 25 seats',
                  'Library Q&A for every seat',
                  'Owner join code for staff',
                ],
                description: 'For firms that need shared access across a team.',
              },
              {
                name: 'Company Large',
                price: '$299 / month',
                features: [
                  'Up to 50 seats',
                  'Library Q&A for every seat',
                  'Owner join code for staff',
                ],
                description: 'Larger teams on the same product.',
              },
            ].map((plan) => (
              <div
                key={plan.name}
                className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="text-lg font-semibold text-slate-950">{plan.name}</h3>
                  <span className="text-sm font-semibold text-[#9a7a35]">{plan.price}</span>
                </div>
                <p className="mt-2 text-sm text-slate-600">{plan.description}</p>
                <ul className="mt-3 space-y-1 text-sm text-slate-700 list-disc list-inside">
                  {plan.features.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-slate-950">Get started</h2>
          <p className="text-slate-700 leading-relaxed">
            Create an account, open the country library, and verify answers against official publications and qualified
            professionals.
          </p>
          <div className="flex flex-wrap gap-3">
            <a href="/signup" className="augusta-button-primary inline-flex items-center px-5 py-3">
              Sign up
            </a>
            <a href="/pricing" className="augusta-button-secondary inline-flex items-center px-5 py-3">
              Pricing
            </a>
            <a href="/login" className="augusta-button-secondary inline-flex items-center px-5 py-3">
              Log in
            </a>
          </div>
        </section>
      </div>
    </div>
  );
};

export default AboutApp;
