import { API_BASE, googleKey } from './config';

test('API base URL has no trailing slash and defaults to the local backend', () => {
  expect(API_BASE).toBe('http://127.0.0.1:8000');
});

test('Google key parameter is omitted when no key is configured', () => {
  expect(googleKey('?')).toBe('');
  expect(googleKey('&')).toBe('');
});
