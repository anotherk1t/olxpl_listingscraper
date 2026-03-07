/**
 * OLX DB MCP Extension
 * Exposes read-only SQL access to olx.db for Gemini CLI tool-use.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import Database from 'better-sqlite3';
import path from 'path';

const DB_PATH = process.env.OLX_DB_PATH || path.join(import.meta.dirname, '..', 'data', 'olx.db');

let db;
try {
  db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
} catch (err) {
  console.error(`[olx-db-ext] Failed to open database at ${DB_PATH}: ${err.message}`);
  process.exit(1);
}

const server = new McpServer({
  name: 'olx-db',
  version: '1.0.0',
});

server.registerTool(
  'get_schema',
  {
    description: 'Get the full database schema (all tables, columns, types, and relationships). Call this first to understand what data is available.',
    inputSchema: z.object({}).shape,
  },
  async () => {
    const tables = db.prepare(
      "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).all();
    const schema = tables.map(t => t.sql).join('\n\n');
    return {
      content: [{ type: 'text', text: schema }],
    };
  },
);

server.registerTool(
  'query_olx_db',
  {
    description: 'Execute a read-only SQL query against the OLX scraper database. Only SELECT statements are allowed. Returns JSON rows. Use get_schema first to understand the tables.',
    inputSchema: z.object({
      sql: z.string().describe('The SQL SELECT query to execute'),
    }).shape,
  },
  async ({ sql: query }) => {
    // Enforce read-only
    const trimmed = query.trim().toUpperCase();
    if (!trimmed.startsWith('SELECT') && !trimmed.startsWith('WITH') && !trimmed.startsWith('EXPLAIN')) {
      return {
        content: [{ type: 'text', text: 'Error: Only SELECT/WITH/EXPLAIN queries are allowed.' }],
        isError: true,
      };
    }

    try {
      const rows = db.prepare(query).all();
      const result = JSON.stringify(rows, null, 2);
      // Truncate if too large
      if (result.length > 50000) {
        return {
          content: [{ type: 'text', text: result.slice(0, 50000) + '\n... (truncated)' }],
        };
      }
      return {
        content: [{ type: 'text', text: result || '[]' }],
      };
    } catch (err) {
      return {
        content: [{ type: 'text', text: `SQL Error: ${err.message}` }],
        isError: true,
      };
    }
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
