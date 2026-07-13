const http = require('http');
const fs = require('fs');
const path = require('path');
const { randomUUID } = require('crypto');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'text/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.svg':  'image/svg+xml',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.json': 'application/json; charset=utf-8',
  '.ico':  'image/x-icon',
  '.mp4':  'video/mp4',
  '.webm': 'video/webm',
};

function contentType(p) {
  return MIME[path.extname(p).toLowerCase()] || 'application/octet-stream';
}

function json(res, status, body) {
  const s = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(s),
  });
  res.end(s);
}

function notFound(res) {
  res.writeHead(404, { 'Content-Type': 'text/plain' });
  res.end('Not found');
}

function serveStatic(res, filePath) {
  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) return notFound(res);
    res.writeHead(200, {
      'Content-Type': contentType(filePath),
      'Content-Length': stat.size,
      'Cache-Control': 'no-store',
    });
    fs.createReadStream(filePath).pipe(res);
  });
}

function stubApi(req, res, url) {
  const p = url.pathname;

  if (p === '/auth/session') {
    return json(res, 200, { authenticated: false, user: null, mode: 'preview' });
  }
  if (p === '/auth/email/start' || p === '/auth/email/verify') {
    return json(res, 501, { error: 'auth_disabled_in_preview',
      message: 'Sign-in is disabled in the local preview build. Visit lumeri.ai to use the full product.' });
  }
  if (p === '/auth/google/start') {
    return json(res, 501, { error: 'auth_disabled_in_preview' });
  }
  if (p === '/auth/logout') {
    return json(res, 200, { ok: true });
  }
  if (p === '/settings/sandbox') {
    return json(res, 200, { enabled: true });
  }
  if (p === '/sessions' && req.method === 'POST') {
    return json(res, 200, {
      session_id: `preview-${randomUUID().slice(0, 8)}`,
      created_at: new Date().toISOString(),
      mode: 'preview',
    });
  }
  if (p.startsWith('/sessions/')) {
    if (p.endsWith('/stream') || /\/stream(\?|$)/.test(p)) {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-store',
        'Connection': 'keep-alive',
      });
      res.write(': preview stream, no live events\n\n');
      const ka = setInterval(() => { try { res.write(': keep-alive\n\n'); } catch {} }, 15000);
      req.on('close', () => clearInterval(ka));
      return true;
    }
    if (p.endsWith('/timeline')) return json(res, 200, { operations: [] });
    if (p.endsWith('/assets'))   return json(res, 200, { assets: [] });
    if (p.endsWith('/close'))    return json(res, 200, { ok: true });
    if (p.endsWith('/turn'))     return json(res, 501, { error: 'preview_mode',
      message: 'Turn execution requires a backend. This is the local preview build.' });
    if (p.endsWith('/plan_mode')) return json(res, 200, { enabled: false });
    if (p.endsWith('/timeline/op')) return json(res, 200, { ok: true });
    return json(res, 200, {});
  }
  if (p.startsWith('/media-library/')) {
    if (p.startsWith('/media-library/list')) return json(res, 200, { items: [] });
    return json(res, 200, { ok: true });
  }
  return null;
}

function createServer({ staticRoot }) {
  return http.createServer((req, res) => {
    let url;
    try {
      url = new URL(req.url, 'http://127.0.0.1');
    } catch {
      return notFound(res);
    }

    const stubbed = stubApi(req, res, url);
    if (stubbed !== null) return;

    let rel = url.pathname;
    if (rel === '/' || rel === '') rel = '/v3/index.html';
    if (rel === '/v3' || rel === '/v3/') rel = '/v3/index.html';

    const safe = path.normalize(rel).replace(/^(\.\.[\/\\])+/, '');
    const filePath = path.join(staticRoot, safe.replace(/^\/+/, ''));

    if (!filePath.startsWith(staticRoot)) return notFound(res);
    return serveStatic(res, filePath);
  });
}

function start({ staticRoot }) {
  return new Promise((resolve, reject) => {
    const server = createServer({ staticRoot });
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port });
    });
  });
}

module.exports = { start };
