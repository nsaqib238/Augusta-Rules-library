/**
 * Centralized API configuration
 * All API calls should use this base URL
 * In production when served from same host as API, use current origin so no .env is required.
 */
function getApiBase(): string {
  const envUrl = (process.env.REACT_APP_API_URL || process.env.REACT_APP_BACKEND_URL || '').replace(
    /\/$/,
    ''
  );
  // HTTPS page + http:// API URL (non-localhost) = mixed content → browser blocks fetch ("Failed to fetch")
  if (
    typeof window !== 'undefined' &&
    window.location.protocol === 'https:' &&
    envUrl.startsWith('http://') &&
    !/localhost|127\.0\.0\.1/.test(envUrl)
  ) {
    return window.location.origin;
  }
  if (envUrl) {
    return envUrl;
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return 'http://localhost:8000';
}
const API_URL = getApiBase();

// Remove trailing slash if present
export const BASE_API_URL = API_URL.replace(/\/$/, '');

function isLocalDevHost(): boolean {
  if (typeof window === 'undefined') return false;
  const h = window.location.hostname;
  return h === 'localhost' || h === '127.0.0.1';
}

function localBackendUrl(): string {
  const envUrl = (process.env.REACT_APP_API_URL || process.env.REACT_APP_BACKEND_URL || '').replace(
    /\/$/,
    ''
  );
  if (envUrl === 'http://localhost:8000' || envUrl === 'http://localhost:8001') {
    return envUrl;
  }
  return '';
}

/**
 * URL for fetch(). Production uses path-only (/api/...) for same-origin nginx proxy.
 * Local dev uses CRA proxy (relative path) unless REACT_APP_API_URL points at the backend port.
 */
export function apiUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  if (typeof window !== 'undefined') {
    if (!isLocalDevHost()) {
      return p;
    }
    const backend = localBackendUrl();
    if (backend) {
      return `${backend}${p}`;
    }
    return p;
  }
  return `${BASE_API_URL}${p}`;
}

/**
 * Prefix helper for components that still call fetch directly.
 */
export function withApiBase(path: string): string {
  if (!path) return BASE_API_URL;
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  const p = path.startsWith('/') ? path : `/${path}`;
  return apiUrl(p);
}

/**
 * Get the full API endpoint URL
 */
export function getApiUrl(endpoint: string): string {
  const p = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  return apiUrl(p);
}

/**
 * Make an authenticated API request
 */
export async function apiRequest(
  endpoint: string,
  options: RequestInit = {}
): Promise<Response> {
  const url = getApiUrl(endpoint);
  
  // Get auth token from database session
  const { supabase } = await import('./supabase');
  const { data: { session } } = await supabase.auth.getSession();
  
  // Build headers object with proper typing
  const headersObj: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  
  // Merge existing headers if provided
  if (options.headers) {
    if (options.headers instanceof Headers) {
      options.headers.forEach((value, key) => {
        headersObj[key] = value;
      });
    } else if (Array.isArray(options.headers)) {
      options.headers.forEach(([key, value]) => {
        headersObj[key] = value;
      });
    } else {
      Object.assign(headersObj, options.headers as Record<string, string>);
    }
  }
  
  // Add authorization header if session exists
  if (session?.access_token) {
    headersObj['Authorization'] = `Bearer ${session.access_token}`;
  }
  
  return fetch(url, {
    ...options,
    headers: headersObj,
  });
}

export default BASE_API_URL;

