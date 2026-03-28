/**
 * Copilot CLI HTTP Proxy
 * Wraps the local `copilot` CLI over a simple HTTP API so the Python bot
 * (running in Docker) can call it without needing the CLI inside the container.
 *
 * POST /ask  { "prompt": "...", "model": "...", "mcp": true }  -> { "response": "..." }
 * GET  /health                                                  -> { "ok": true }
 */

const http = require('http');
const path = require('path');
const { spawn } = require('child_process');

const PORT = 3000;
const DEFAULT_MODEL = 'gpt-5-mini';
const TIMEOUT_MS = 180000; // 3 minutes per request
const SCRIPT_DIR = __dirname;

/**
 * Strip residual Copilot CLI noise from output.
 * With --silent most UI chrome is removed, but this catches edge cases
 * like leftover footer lines or tool-use indicators.
 */
function stripCopilotNoise(text) {
  // Remove "Total usage est:" footer and everything after
  text = text.replace(/\n\s*Total usage est:[\s\S]*$/, '');
  // Remove tool-use indicator lines (● ... └ ...)
  text = text.replace(/^● .+\n(?:\s+└ .+\n?)*/gm, '');
  return text.trim();
}

/**
 * Run `copilot` CLI asynchronously.
 * @param {string} prompt - The prompt to send.
 * @param {object} opts
 * @param {string}  [opts.model]   - Model override (default: gpt-5-mini).
 * @param {boolean} [opts.mcp]     - Whether to attach the olx-db-ext MCP server.
 * @returns {Promise<{response: string, error: string}>}
 */
function runCopilot(prompt, { model, mcp } = {}) {
  return new Promise((resolve, reject) => {
    const args = [
      '-p', prompt,
      '--allow-all-tools',
      '--output-format', 'text',
      '-s',
      '--model', model || DEFAULT_MODEL,
    ];

    if (mcp) {
      const mcpConfig = path.join(SCRIPT_DIR, 'copilot-mcp-config.json');
      args.push('--additional-mcp-config', `@${mcpConfig}`);
    }

    const child = spawn('copilot', args, { cwd: SCRIPT_DIR });
    let stdout = '';
    let stderr = '';

    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });

    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('Copilot subprocess timed out'));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      const cleaned = stripCopilotNoise(stdout);
      resolve({ response: cleaned, error: stderr.trim() });
    });

    child.on('error', err => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

function handleRequest(req, res) {
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
    let prompt, model, mcp;
    try {
      const parsed = JSON.parse(body);
      prompt = parsed.prompt;
      model = parsed.model;
      mcp = !!parsed.mcp;
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
      const { response, error } = await runCopilot(prompt, { model, mcp });
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
  console.log(`[proxy] Copilot CLI proxy listening on port ${PORT} (model: ${DEFAULT_MODEL})`);
});
