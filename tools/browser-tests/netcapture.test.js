// Checks the browser's network capture (app/src/main/assets/browser/init.js) in headless Chromium:
// fetch/XHR/beacon requests with headers, bodies and timing, and WebSocket messages.
// Run: node tools/browser-tests/netcapture.test.js   (needs: npm i playwright, or a preinstalled one)
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const http = require('http');
const crypto = require('crypto');

// Tiny WebSocket echo server (text frames only), so the page uses the real browser WebSocket.
function wsServer() {
  const srv = http.createServer();
  srv.on('upgrade', (req, sock) => {
    const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    sock.on('data', buf => {
      if ((buf[0] & 0x0f) !== 1) return;
      const len = buf[1] & 0x7f, mask = buf.slice(2, 6), data = buf.slice(6, 6 + len);
      const text = Buffer.from(data.map((b, i) => b ^ mask[i % 4])).toString();
      const out = Buffer.from('echo:' + text);
      sock.write(Buffer.concat([Buffer.from([0x81, out.length]), out]));
    });
  });
  return new Promise(r => srv.listen(0, '127.0.0.1', () => r(srv)));
}

const init = fs.readFileSync(path.join(__dirname, '../../app/src/main/assets/browser/init.js'), 'utf8');

const PAGE = port => `<!doctype html><title>net test</title><body>hi<script>const WS_URL = 'ws://127.0.0.1:${port}/ws';
(async () => {
  const r = await fetch('/api/items?token=abcdefghijklmnop&q=shoes', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer secret-token-123456789' },
    body: JSON.stringify({ name: 'x' }) });
  window.fetched = await r.json();
  const x = new XMLHttpRequest(); x.open('GET', '/api/xhr'); x.setRequestHeader('X-Test', '1');
  x.onload = () => { window.xhrDone = true; }; x.send();
  try { await fetch('/api/fail'); } catch (e) {}
  navigator.sendBeacon('/api/beacon', 'ping=1');
  const ws = new WebSocket(WS_URL);
  ws.onopen = () => ws.send('hello');
  ws.onmessage = e => { window.wsGot = e.data; };
})();
</script></body>`;

(async () => {
  const srv = await wsServer();
  const port = srv.address().port;
  const browser = await chromium.launch({ args: ['--disable-features=LocalNetworkAccessChecks,BlockInsecurePrivateNetworkRequests'] });
  const page = await browser.newPage();
  await page.addInitScript(init);
  await page.route(`http://127.0.0.1:${port}/**`, route => {
    const u = new URL(route.request().url());
    if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: PAGE(port) });
    if (u.pathname === '/api/items') return route.fulfill({ status: 201, contentType: 'application/json', headers: { 'X-Server': 'unit' }, body: JSON.stringify({ ok: true, echo: route.request().postData() }) });
    if (u.pathname === '/api/xhr') return route.fulfill({ contentType: 'text/plain', body: 'plain text' });
    if (u.pathname === '/api/fail') return route.abort();
    return route.fulfill({ status: 204, body: '' });
  });
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.waitForFunction(() => window.fetched && window.xhrDone && window.wsGot, null, { timeout: 10000 });
  await page.waitForTimeout(300);
  const net = await page.evaluate(() => window.__lfNet);
  const sockets = await page.evaluate(() => window.__lfWs);

  const post = net.find(e => e.url.includes('/api/items'));
  assert.ok(post, 'fetch captured');
  assert.strictEqual(post.kind, 'fetch');
  assert.strictEqual(post.method, 'POST');
  assert.strictEqual(post.status, 201);
  assert.strictEqual(post.reqHeaders['authorization'], 'Bearer secret-token-123456789');
  assert.strictEqual(post.reqBody, '{"name":"x"}');
  assert.strictEqual(post.resHeaders['x-server'], 'unit');
  assert.ok(post.body.includes('"ok":true'), 'response body kept');
  assert.ok(typeof post.time === 'number', 'timing kept');

  const xhr = net.find(e => e.kind === 'xhr');
  assert.ok(xhr, 'xhr captured');
  assert.strictEqual(xhr.reqHeaders['x-test'], '1');
  assert.strictEqual(xhr.body, 'plain text');
  assert.strictEqual(xhr.status, 200);

  const fail = net.find(e => e.url.includes('/api/fail'));
  assert.ok(fail && fail.error, 'failed request marked');

  const beacon = net.find(e => e.kind === 'beacon');
  assert.ok(beacon && beacon.reqBody === 'ping=1', 'beacon captured');

  assert.strictEqual(sockets.length, 1);
  assert.strictEqual(sockets[0].state, 'open');
  assert.deepStrictEqual(sockets[0].msgs.map(m => m.dir + ':' + m.data), ['out:hello', 'in:echo:hello']);
  assert.strictEqual(await page.evaluate(() => window.wsGot), 'echo:hello');
  // The page still sees a normal WebSocket.
  assert.strictEqual(await page.evaluate(() => WebSocket.OPEN), 1);

  const timing = await page.evaluate(() => performance.getEntriesByType('resource').map(e => e.name));
  assert.ok(timing.some(n => n.includes('/api/items')), 'resource timing available');

  // The Resource Timing query NetInspect.kt runs for <network all/> and <network detail/>.
  const kt = fs.readFileSync(path.join(__dirname, '../../app/src/main/java/com/localforge/app/agent/NetInspect.kt'), 'utf8');
  const expr = kt.match(/WebBrowser\.eval\("""(performance[\s\S]*?)""", 8_000\)/)[1];
  const rows = await page.evaluate(expr);
  const item = rows.find(r => r.url.includes('/api/items'));
  assert.ok(item && item.kind === 'fetch' && typeof item.duration === 'number', 'timing rows readable');
  assert.ok(rows.some(r => r.kind === 'navigation'), 'page itself listed');

  await browser.close();
  srv.close();
  console.log('netcapture: all checks passed (' + net.length + ' calls, ' + sockets.length + ' socket)');
})().catch(e => { console.error(e); process.exit(1); });
