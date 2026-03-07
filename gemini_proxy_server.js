/**
 * Gemini CLI HTTP Proxy
 * Wraps the local `gemini` CLI over a simple HTTP API so the Python bot
 * can call it without needing Node.js inside the Python container.
 *
 * POST /ask  { "prompt": "..." }  -> { "response": "..." }
 * GET  /health                    -> { "ok": true }
 */

const http = require('http');
const { spawn } = require('child_process');

const PORT = 3000;
const TIMEOUT_MS = 180000; // 3 minutes per request

/**
 * Run `gemini` CLI asynchronously.
 * Accepts optional model override and extensions list.
 * Returns a Promise that resolves to { response, error }.
 */
function runGemini(prompt, { model, extensions, approvalMode } = {}) {
  return new Promise((resolve, reject) => {
    const args = [
      '-m', model || 'gemini-3-flash-preview',
      '-p', prompt,
      '-o', 'text',
    ];
    if (approvalMode) {
      args.push('--approval-mode', approvalMode);
    }
    if (extensions && Array.isArray(extensions)) {
      for (const ext of extensions) {
        args.push('-e', ext);
      }
    }
    const child = spawn('gemini', args);
    let stdout = '';
    let stderr = '';

    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });

    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('Gemini subprocess timed out'));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      resolve({ response: stdout.trim(), error: stderr.trim() });
    });

    child.on('error', err => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

function handleRequest(req, res) {
  // Health check
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  if (req.method !== 'POST' || req.url !== '/ask') {
    res.writeHead(404);
    res.end();
    return;
  }

  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', async () => {
    let prompt, model, extensions, approvalMode;
    try {
      const parsed = JSON.parse(body);
      prompt = parsed.prompt;
      model = parsed.model;
      extensions = parsed.extensions;
      approvalMode = parsed.approval_mode;
    } catch {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Invalid JSON body' }));
      return;
    }

    if (!prompt || typeof prompt !== 'string') {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Missing "prompt" field' }));
      return;
    }

    try {
      const { response, error } = await runGemini(prompt, { model, extensions, approvalMode });
      if (error) console.warn(`[proxy] stderr: ${error}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ response, error: error || null }));
    } catch (err) {
      console.error(`[proxy] error: ${err.message}`);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ response: '', error: err.message }));
    }
  });
}

const server = http.createServer(handleRequest);
server.listen(PORT, () => {
  console.log(`[proxy] Gemini CLI proxy listening on port ${PORT}`);
});
