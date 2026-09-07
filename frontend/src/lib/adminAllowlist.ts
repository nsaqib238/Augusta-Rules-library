const raw = process.env.REACT_APP_ADMIN_EMAIL_ALLOWLIST || '';

const ADMIN_EMAIL_ALLOWLIST = new Set(
  raw
    .split(',')
    .map((email) => email.trim().toLowerCase())
    .filter(Boolean)
);

export function isAdminEmailAllowlisted(email: string | undefined | null): boolean {
  if (!email) return false;
  return ADMIN_EMAIL_ALLOWLIST.has(email.trim().toLowerCase());
}
