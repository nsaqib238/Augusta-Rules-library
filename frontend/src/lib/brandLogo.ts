/**
 * Brand logos in `public/img/` (synced from `frontend/img/` via npm run sync-logo).
 * - AugustaSearch-wordmark.png — tight crop on white for header/login (consistent size).
 * - NewOfficetools.png — full asset (dark background) for legacy/marketing if needed.
 */
const publicUrl = process.env.PUBLIC_URL || '';

function logoUrl(filename: string): string {
  const path = `${publicUrl}/img/${filename}`;
  // Dev: bust cache when replacing PNGs locally.
  if (process.env.NODE_ENV === 'development') {
    return `${path}?t=${Date.now()}`;
  }
  // Prod: stable version query when the header asset changes (avoids stale CDN/browser cache).
  return `${path}?v=3`;
}

/** Header / auth UI — cropped wordmark on white. */
export const headerBrandLogoUrl = logoUrl('AugustaSearch-wordmark.png');

/** Full logo file (optional). */
export const brandLogoUrl = logoUrl('NewOfficetools.png');
