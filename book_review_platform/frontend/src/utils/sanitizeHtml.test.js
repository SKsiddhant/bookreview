import { sanitizeHtml, decodeEntities } from './sanitizeHtml';

test('keeps basic formatting', () => {
  expect(sanitizeHtml('<p>Hello <b>world</b><br>x</p>')).toBe('<p>Hello <b>world</b><br>x</p>');
});

test('removes scripts, event handlers and dangerous elements', () => {
  const dirty = '<img src=x onerror="alert(1)"><script>alert(2)</script><iframe src="//evil"></iframe>' +
    '<a href="javascript:alert(3)" onclick="x()">link</a><p onmouseover="y()">t</p>';
  const out = sanitizeHtml(dirty);
  expect(out).not.toMatch(/script|onerror|onclick|onmouseover|javascript:|iframe|<img|<a /i);
  expect(out).toContain('link');
  expect(out).toContain('<p>t</p>');
});

test('entity-encoded markup is neutralised after decoding (the old code path)', () => {
  const decoded = decodeEntities('&lt;img src=x onerror=alert(1)&gt; hi &rsquo;');
  const out = sanitizeHtml(decoded);
  expect(out).not.toMatch(/<img|onerror/i);
  expect(out).toContain('hi');
});

test('handles empty and non-string input', () => {
  expect(sanitizeHtml(null)).toBe('');
  expect(sanitizeHtml(undefined)).toBe('');
  expect(sanitizeHtml(42)).toBe('42');
});

test('does not execute payloads while parsing', () => {
  window.__pwned = false;
  sanitizeHtml('<img src=x onerror="window.__pwned=true">');
  return new Promise((r) => setTimeout(r, 20)).then(() => expect(window.__pwned).toBe(false));
});
