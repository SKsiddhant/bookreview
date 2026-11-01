// Backend base URL, without a trailing slash. Override with REACT_APP_API_URL (see .env.example).
export const API_BASE = (process.env.REACT_APP_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');

// Google Books key. It ends up in the public bundle, so restrict it by HTTP referrer in the
// Google Cloud console. Without a key the API still answers, with a much lower quota.
const GOOGLE_BOOKS_API_KEY = process.env.REACT_APP_GOOGLE_BOOKS_KEY || '';

// '' when no key is configured (an empty key= parameter is rejected by Google), otherwise
// '<prefix>key=...' where prefix is '?' or '&' depending on the URL it is appended to.
export const googleKey = (prefix) =>
  GOOGLE_BOOKS_API_KEY ? `${prefix}key=${encodeURIComponent(GOOGLE_BOOKS_API_KEY)}` : '';
