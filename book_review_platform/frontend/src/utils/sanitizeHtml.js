// Minimal allow-list HTML sanitiser for third-party book descriptions.
//
// Parsing happens in an inert document (DOMParser): assigning untrusted markup to the innerHTML of
// any element in the live page - even a detached one - starts loading <img onerror=...> payloads.
const ALLOWED = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'P', 'BR', 'UL', 'OL', 'LI', 'BLOCKQUOTE']);
const DROP_WITH_CONTENT = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'MATH']);

function clean(node, doc) {
  [...node.childNodes].forEach((child) => {
    if (child.nodeType === 3) return; // text
    if (child.nodeType !== 1) {
      child.remove(); // comments etc.
      return;
    }
    if (DROP_WITH_CONTENT.has(child.tagName.toUpperCase())) {
      child.remove();
      return;
    }
    clean(child, doc);
    if (ALLOWED.has(child.tagName)) {
      [...child.attributes].forEach((a) => child.removeAttribute(a.name));
    } else {
      child.replaceWith(...child.childNodes); // keep the text, drop the tag
    }
  });
}

export function sanitizeHtml(html) {
  const doc = new DOMParser().parseFromString(`<body>${String(html ?? '')}</body>`, 'text/html');
  clean(doc.body, doc);
  return doc.body.innerHTML;
}

export function decodeEntities(text) {
  return new DOMParser().parseFromString(String(text ?? ''), 'text/html').documentElement.textContent;
}
