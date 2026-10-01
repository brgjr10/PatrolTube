// Minimal DOM stub: records what the card builder creates, and — critically —
// reports whether anything landed via an HTML-parsing sink instead of textContent.
const fs = require('fs');
const path = require('path');

function makeEl(tag) {
  return {
    tagName: tag,
    _text: '',
    style: {},
    children: [],
    attrs: {},
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; },
    set innerHTML(v) { throw new Error('innerHTML sink used with: ' + String(v).slice(0, 80)); },
    get innerHTML() { return ''; },
    appendChild(child) { this.children.push(child); return child; },
    set className(v) { this.attrs.className = v; },
    get className() { return this.attrs.className; },
    set href(v) { this.attrs.href = v; },
    set src(v) { this.attrs.src = v; },
    set alt(v) { this.attrs.alt = v; },
    set target(v) { this.attrs.target = v; },
    set rel(v) { this.attrs.rel = v; },
    set loading(v) { this.attrs.loading = v; },
  };
}

const doc = {
  createElement: makeEl,
  createDocumentFragment: () => makeEl('#fragment'),
};

const template = fs.readFileSync(path.join(__dirname, '..', 'templates', process.argv[2]), 'utf8');

function extract(name) {
  const start = template.indexOf('function ' + name + '(');
  if (start === -1) throw new Error('function ' + name + ' not found in template');
  let depth = 0, i = template.indexOf('{', start);
  for (let j = i; j < template.length; j++) {
    if (template[j] === '{') depth++;
    else if (template[j] === '}') { depth--; if (depth === 0) return template.slice(start, j + 1); }
  }
  throw new Error('unbalanced braces in ' + name);
}

const helpers = [
  'safeVideoUrl',
  'buildVideoCard',
  'formatDuration',
  'formatViews',
  'parseUploadDate',
  'formatUploadDate',
  'scoreClass',
].map(extract).join('\n\n');

const factory = new Function(
  'document', 'formatDuration', 'formatViews', 'formatUploadDate', 'scoreClass',
  helpers + '\nreturn buildVideoCard;'
);
const buildVideoCard = factory(doc, () => '1:00', () => '1,000 views', () => '', () => 'score-high');

const PAYLOAD = '<img src=x onerror="window.__XSS__=1;document.title=\'PWNED-TITLE\'">';
const hostile = {
  id: 'dQw4w9WgXcQ',
  title: PAYLOAD,
  channel: PAYLOAD,
  url: 'javascript:window.__XSS_URL__=1',
  thumbnail: '"><img src=x onerror="window.__XSS_THUMB__=1">',
  duration: 60,
  view_count: 1,
  upload_date: '20260101',
  confidence: 99,
  cam_score: 100,
  ohio_score: 100,
  match_reason: PAYLOAD,
  matched_cities: [PAYLOAD],
};

const card = buildVideoCard(hostile);
const failures = [];

function walk(node, visit) {
  visit(node);
  (node.children || []).forEach(c => walk(c, visit));
}

// The stub's innerHTML setter already throws, so reaching here means nothing went
// through an HTML-parsing sink. What remains: no attribute may carry an inline
// event handler, and no URL-bearing attribute may carry a non-YouTube value.
walk(card, node => {
  Object.entries(node.attrs || {}).forEach(([k, v]) => {
    const name = k.toLowerCase();
    if (name.startsWith('on')) failures.push(node.tagName + ' carries inline handler ' + k);
    if ((name === 'href' || name === 'src') && v && !/^https:\/\/(www\.)?(youtube\.com|i\.ytimg\.com)\//.test(v)) {
      failures.push(node.tagName + '.' + name + ' is not a YouTube URL: ' + v);
    }
  });
  if (node.children.length && node.tagName === 'img') failures.push('element built inside <img>');
});

const body = card.children.find(c => c.className === 'body');
const titleLink = body.children.find(c => c.className === 'title');
if (titleLink.textContent !== PAYLOAD) failures.push('title did not land verbatim as textContent: ' + titleLink.textContent);
if (titleLink.attrs.href !== 'https://www.youtube.com/watch?v=dQw4w9WgXcQ') failures.push('unsafe href not normalised: ' + titleLink.attrs.href);
if (titleLink.attrs.rel !== 'noopener noreferrer') failures.push('missing rel=noopener');

const thumb = card.children.find(c => c.className === 'thumb').children.find(c => c.tagName === 'img');
if (thumb.attrs.src !== '') failures.push('unsafe thumbnail src not rejected: ' + thumb.attrs.src);
if (!thumb.attrs.alt) failures.push('thumbnail has no alt text (PATROLTUBE-017)');

const channel = body.children.find(c => c.className === 'channel');
if (channel.textContent !== PAYLOAD) failures.push('channel did not land as textContent');

const meta = body.children.find(c => c.className === 'meta');
if (!meta.children.every(t => t.tagName === 'span')) failures.push('tag built with a non-span element');
if (meta.children.length < 4) failures.push('matched_cities / cam / ohio tags were dropped: ' + meta.children.length);

if (failures.length) {
  console.error('FAIL ' + process.argv[2] + '\n  - ' + failures.join('\n  - '));
  process.exit(1);
}
console.log('PASS ' + process.argv[2]);
