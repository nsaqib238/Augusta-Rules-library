/** Contact / support address (set REACT_APP_SUPPORT_EMAIL in frontend/.env). */
export const SUPPORT_EMAIL = (
  process.env.REACT_APP_SUPPORT_EMAIL || 'naajm@augustasearch.com'
).trim();

export function supportMailto(subject?: string): string {
  const base = `mailto:${SUPPORT_EMAIL}`;
  return subject ? `${base}?subject=${encodeURIComponent(subject)}` : base;
}
