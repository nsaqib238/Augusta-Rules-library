/**
 * TypeScript declarations for Stripe Pricing Table custom element
 */
declare namespace JSX {
  interface IntrinsicElements {
    'stripe-pricing-table': React.DetailedHTMLProps<
      React.HTMLAttributes<HTMLElement> & {
        'pricing-table-id': string;
        'publishable-key': string;
      },
      HTMLElement
    >;
  }
}

