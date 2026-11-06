/*
 * せいじのまど 検索API・MCP（Cloudflare Workers）
 *
 * ルール S-1：公開しているデータ（data/*.json と同じもの。pipeline/db.py が D1 に入れる）を読んで返すだけ。書き込み・ログインはない。
 * ルール S-2：使う人ごとの記録はとらない。検索のことばも保存しない（このプログラムはログを出さない）。
 *            使いすぎを防ぐため、接続元ごとの回数を1分だけ数える（Cloudflare の rate limiting。保存はしない）。
 * ルール S-3：申し込みなしで、だれでも同じ条件。上限は LIMIT_PER_MIN（公開）。
 *
 *   GET  /                      … 使い方（JSON）
 *   GET  /v1/search?q=&type=    … 検索
 *   GET  /v1/types              … 種類と件数
 *   GET  /v1/{type}             … 一覧（limit・offset）
 *   GET  /v1/{type}/{id}        … 1件と、そのつながり
 *   POST /mcp                   … AI からの入口（Model Context Protocol。Streamable HTTP・状態を持たない）
 */
const VERSION = '1.0.0';
const LIMIT_PER_MIN = 60;
const TYPES = ['law', 'bill', 'question', 'party', 'kaiha', 'member', 'governor', 'org', 'council', 'page', 'term'];
const TYPE_ORDER = 'CASE e.type ' + TYPES.map((t, i) => `WHEN '${t}' THEN ${i}`).join(' ') + ' ELSE 99 END';
const SITE = 'https://seijinomado.com/';
const LICENSE = { name: 'CC BY 4.0', url: 'https://creativecommons.org/licenses/by/4.0/deed.ja', attribution: '出典として「せいじのまど」の名前と、使ったページか API の URL を示してください。' };
const ABOUT = {
  name: 'せいじのまど 検索API・MCP',
  about: '日本の政治の情報を、どの党にもメディアにも偏らずに届けるサイト「せいじのまど」のデータを、検索とつながりで引けるAPI。中身はサイト・静的API（' + SITE + 'api/v1/index.json）と同じで、毎日自動で更新される。事実と意見を分け、結論は書かない。',
  rules: {
    'S-1': 'サーバーは、公開しているデータを読むだけに使う（ログイン・書き込みはない）',
    'S-2': '使う人ごとの記録をとらない（検索のことばも保存しない）',
    'S-3': '中身はだれでも同じ。無料で、申し込みなしで使える。有料枠で変わるのは量（回数の上限など）だけ',
    'S-4': 'お金を払った人や団体が、中身・並び順に関わることはない',
  },
  limits: { per_minute: LIMIT_PER_MIN, note: '接続元ごとに1分あたりの回数。超えると 429 を返す。' },
  order: '検索の結果は、名前と同じ → 名前・よみに含む → 本文に含む の順。同じなら種類の順（' + TYPES.join('・') + '）、その中はサイトの並びの順。人が順番を決めることはない。',
  rules_page: SITE + 'about/',
  license: LICENSE,
};

const J = (obj, status = 200, extra = {}) => new Response(JSON.stringify(obj), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'access-control-allow-origin': '*', 'cache-control': status === 200 ? 'public, max-age=300, s-maxage=600' : 'no-store', ...extra },
});

async function meta(env) {
  const { results } = await env.DB.prepare('SELECT key, value FROM meta').all();
  const m = {};
  for (const r of results) m[r.key] = r.value;
  return { built: m.built, counts: m.counts ? JSON.parse(m.counts) : {}, edges: Number(m.edges || 0), types: m.types ? JSON.parse(m.types) : {} };
}

function row(r, full) {
  const o = { type: r.type, id: r.id, name: r.name };
  if (r.kana) o.kana = r.kana;
  if (r.summary) o.summary = r.summary;
  if (r.url) o.page = r.url;
  if (full) {
    if (r.api) o.api = r.api;
    if (r.checked) o.checked = r.checked;
    if (r.data) Object.assign(o, { data: JSON.parse(r.data) });
  }
  return o;
}

const clampInt = (v, d, lo, hi) => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d; };

async function search(env, query, type, limit = 20, offset = 0) {
  const words = String(query || '').normalize('NFKC').trim().split(/\s+/).filter(Boolean).slice(0, 5);
  if (!words.length) return { error: 'q（検索することば）が空です。' };
  if (type && !TYPES.includes(type)) return { error: 'type は ' + TYPES.join('・') + ' のどれかです。' };
  const long = words.filter((w) => [...w].length >= 3), short = words.filter((w) => [...w].length < 3);
  const binds = [];
  const first = words[0];
  let sql = `SELECT e.type, e.id, e.name, e.kana, e.summary, e.url, CASE WHEN e.name = ?1 THEN 0 WHEN instr(e.name, ?1) > 0 OR instr(e.kana, ?1) > 0 THEN 1 ELSE 2 END AS m `;
  binds.push(first);
  const where = [];
  if (long.length) {
    sql += 'FROM fts JOIN entities e ON e.type = fts.type AND e.id = fts.id ';
    binds.push(long.map((w) => '"' + w.replace(/"/g, '""') + '"').join(' AND '));
    where.push(`fts MATCH ?${binds.length}`);
  } else {
    sql += 'FROM entities e ';
  }
  for (const w of short) {
    binds.push(w);
    const n = binds.length;
    where.push(`(instr(e.name, ?${n}) > 0 OR instr(e.kana, ?${n}) > 0 OR instr(e.summary, ?${n}) > 0 OR instr(e.body, ?${n}) > 0)`);
  }
  if (type) { binds.push(type); where.push(`e.type = ?${binds.length}`); }
  binds.push(limit + 1, offset);
  sql += 'WHERE ' + where.join(' AND ') + ` ORDER BY m, ${TYPE_ORDER}, e.ord LIMIT ?${binds.length - 1} OFFSET ?${binds.length}`;
  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  const more = results.length > limit;
  return { query: words.join(' '), type: type || null, offset, results: results.slice(0, limit).map((r) => ({ ...row(r), match: ['名前と同じ', '名前・よみに含む', '本文に含む'][r.m] })), next_offset: more ? offset + limit : null };
}

async function list(env, type, limit = 50, offset = 0) {
  if (!TYPES.includes(type)) return { error: 'type は ' + TYPES.join('・') + ' のどれかです。' };
  const { results } = await env.DB.prepare('SELECT type, id, name, kana, summary, url FROM entities WHERE type = ?1 ORDER BY ord LIMIT ?2 OFFSET ?3').bind(type, limit + 1, offset).all();
  const more = results.length > limit;
  return { type, offset, results: results.slice(0, limit).map((r) => row(r)), next_offset: more ? offset + limit : null };
}

async function get(env, type, id) {
  if (!TYPES.includes(type)) return { error: 'type は ' + TYPES.join('・') + ' のどれかです。' };
  const r = await env.DB.prepare('SELECT * FROM entities WHERE type = ?1 AND id = ?2').bind(type, id).first();
  if (!r) return { error: '見つかりません：' + type + ' ' + id, hint: '/v1/search で探せます。' };
  const out = await env.DB.prepare('SELECT ed.rel, ed.dst_type AS type, ed.dst_id AS id, ed.note, e.name, e.url FROM edges ed LEFT JOIN entities e ON e.type = ed.dst_type AND e.id = ed.dst_id WHERE ed.src_type = ?1 AND ed.src_id = ?2').bind(type, id).all();
  const inn = await env.DB.prepare('SELECT ed.rel, ed.src_type AS type, ed.src_id AS id, ed.note, e.name, e.url FROM edges ed LEFT JOIN entities e ON e.type = ed.src_type AND e.id = ed.src_id WHERE ed.dst_type = ?1 AND ed.dst_id = ?2').bind(type, id).all();
  const tidy = (x) => { const o = { rel: x.rel, type: x.type, id: x.id, name: x.name }; if (x.note) o.note = x.note; if (x.url) o.page = x.url; return o; };
  return { ...row(r, true), links: { to: out.results.map(tidy), from: inn.results.map(tidy) }, links_note: 'to は、この項目から出るつながり（例：議員 → 所属会派）。from は、この項目に向かうつながり（例：会派 → この法律への賛成）。' };
}

/* ---------- MCP（Model Context Protocol） ---------- */
const PROTOCOLS = ['2025-06-18', '2025-03-26', '2024-11-05'];
const TYPE_ENUM = { type: 'string', enum: TYPES, description: 'law＝国会で決まったこと（成立した法律・予算・条約と否決された法律案）、bill＝審議中・採決されなかった法律案、question＝質問主意書と答弁書、party＝政党、kaiha＝会派、member＝国会議員、governor＝知事、org＝政府・国会の組織、council＝国の審議会、page＝解説・データのページ、term＝ことば' };
const TOOLS = [
  {
    name: 'search',
    description: 'せいじのまどのデータを、ことばで探す（法律・法律案・質問主意書・政党・会派・国会議員・知事・省庁などの組織・解説のページ・ことば）。日本語で探す。結果は機械で決まる順で、人が選んだ順ではない。Search Japanese political data (laws, bills, parties, Diet members, ministries, explainers). Query in Japanese.',
    inputSchema: { type: 'object', properties: { query: { type: 'string', description: '探すことば（例：防衛、消費税、高市、厚生労働省）。空白で区切ると、すべてを含むものを探す。' }, type: TYPE_ENUM, limit: { type: 'integer', minimum: 1, maximum: 50, default: 20 }, offset: { type: 'integer', minimum: 0, default: 0 } }, required: ['query'] },
  },
  {
    name: 'get',
    description: '1件をくわしく見る。中身（事実・審議の経過・会派ごとの賛否・議員一人ひとりの賛否・公式サイトとSNSなど）と、つながり（議員→所属会派、法律→担当の省庁、会派→法律への賛否 など）を返す。type と id は search の結果のものを使う。Get one item with its data and links.',
    inputSchema: { type: 'object', properties: { type: TYPE_ENUM, id: { type: 'string', description: 'search や list の結果の id' } }, required: ['type', 'id'] },
  },
  {
    name: 'list',
    description: '種類ごとに、すべてを並べる（サイトの並びの順。法律は新しい順、議員は公式の一覧の順など）。List all items of a type.',
    inputSchema: { type: 'object', properties: { type: TYPE_ENUM, limit: { type: 'integer', minimum: 1, maximum: 100, default: 50 }, offset: { type: 'integer', minimum: 0, default: 0 } }, required: ['type'] },
  },
  {
    name: 'about',
    description: 'このデータについて（何が入っているか・件数・更新日・並び順の決まり・ライセンス・ルール）。About this dataset.',
    inputSchema: { type: 'object', properties: {} },
  },
];
const INSTRUCTIONS = 'せいじのまど（' + SITE + '）のデータを引くための入口です。答えるときは、返ってきた事実と出典（page の URL）をそのまま使い、党や議員への評価を足さないでください。会派の主張は「その会派の主張」として扱ってください。引用するときは「せいじのまど」と URL を示してください（CC BY 4.0）。';

async function callTool(env, name, args = {}) {
  if (name === 'search') return search(env, args.query, args.type, clampInt(args.limit, 20, 1, 50), clampInt(args.offset, 0, 0, 100000));
  if (name === 'get') return get(env, args.type, String(args.id || ''));
  if (name === 'list') return list(env, args.type, clampInt(args.limit, 50, 1, 100), clampInt(args.offset, 0, 0, 100000));
  if (name === 'about') return { ...ABOUT, ...(await meta(env)) };
  return null;
}

async function rpc(env, msg) {
  const ok = (result) => ({ jsonrpc: '2.0', id: msg.id, result });
  const err = (code, message) => ({ jsonrpc: '2.0', id: msg.id ?? null, error: { code, message } });
  if (!msg || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string') return err(-32600, 'Invalid Request');
  if (msg.id === undefined) return null; // 通知には返事をしない
  const p = msg.params || {};
  switch (msg.method) {
    case 'initialize':
      return ok({ protocolVersion: PROTOCOLS.includes(p.protocolVersion) ? p.protocolVersion : PROTOCOLS[0], capabilities: { tools: { listChanged: false } }, serverInfo: { name: 'seijinomado', title: 'せいじのまど', version: VERSION }, instructions: INSTRUCTIONS });
    case 'ping':
      return ok({});
    case 'tools/list':
      return ok({ tools: TOOLS });
    case 'tools/call': {
      const r = await callTool(env, p.name, p.arguments || {});
      if (r === null) return err(-32602, 'Unknown tool: ' + p.name);
      return ok({ content: [{ type: 'text', text: JSON.stringify(r) }], structuredContent: r, isError: !!r.error });
    }
    default:
      return err(-32601, 'Method not found: ' + msg.method);
  }
}

async function mcp(req, env) {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'POST, OPTIONS', 'access-control-allow-headers': 'content-type, accept, mcp-protocol-version, mcp-session-id' } });
  if (req.method !== 'POST') return new Response('POST only (Streamable HTTP, stateless)', { status: 405, headers: { allow: 'POST, OPTIONS' } });
  let body;
  try { body = await req.json(); } catch { return J({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }, 400); }
  const many = Array.isArray(body);
  const replies = (await Promise.all((many ? body : [body]).slice(0, 20).map((m) => rpc(env, m)))).filter(Boolean);
  const h = { 'content-type': 'application/json', 'access-control-allow-origin': '*', 'cache-control': 'no-store' };
  if (!replies.length) return new Response(null, { status: 202, headers: h });
  return new Response(JSON.stringify(many ? replies : replies[0]), { status: 200, headers: h });
}

/* ---------- 入口 ---------- */
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    // 使いすぎを防ぐ（S-2：接続元ごとの回数を1分だけ数える。保存しない）
    if (env.LIMITER) {
      const { success } = await env.LIMITER.limit({ key: req.headers.get('cf-connecting-ip') || 'unknown' });
      if (!success) return J({ error: '1分あたりの回数の上限（' + LIMIT_PER_MIN + '回）をこえました。少し待ってから、もう一度ためしてください。' }, 429, { 'retry-after': '60' });
    }
    if (url.pathname === '/mcp') return mcp(req, env);
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'GET, OPTIONS' } });
    if (req.method !== 'GET' && req.method !== 'HEAD') return J({ error: 'GET だけです（MCP は POST /mcp）。' }, 405);

    const cache = caches.default;
    const hit = await cache.match(req);
    if (hit) return hit;
    let res;
    try {
      res = await route(url, env);
    } catch (e) {
      return J({ error: 'データを読めませんでした。少し待ってから、もう一度ためしてください。' }, 503);
    }
    if (res.status === 200) ctx.waitUntil(cache.put(req, res.clone()));
    return res;
  },
};

async function route(url, env) {
  const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent);
  const sp = url.searchParams;
  const wrap = async (data) => (data.error ? J(data, 400) : J({ site: 'せいじのまど', license: LICENSE, built: (await meta(env)).built, ...data }));
  if (!parts.length) {
    const base = url.origin + '/';
    return J({
      ...ABOUT, ...(await meta(env)), endpoints: {
        search: base + 'v1/search?q=防衛', types: base + 'v1/types', list: base + 'v1/law', get: base + 'v1/{type}/{id}', mcp: base + 'mcp（POST・Model Context Protocol）',
        static_api: SITE + 'api/v1/index.json',
      },
    });
  }
  if (parts[0] !== 'v1') return J({ error: '見つかりません。使い方は ' + url.origin + '/ にあります。' }, 404);
  if (parts[1] === 'search') return wrap(await search(env, sp.get('q'), sp.get('type'), clampInt(sp.get('limit'), 20, 1, 50), clampInt(sp.get('offset'), 0, 0, 100000)));
  if (parts[1] === 'types' && parts.length === 2) return wrap(await meta(env));
  if (parts.length === 2) return wrap(await list(env, parts[1], clampInt(sp.get('limit'), 50, 1, 100), clampInt(sp.get('offset'), 0, 0, 100000)));
  if (parts.length >= 3) {
    const r = await get(env, parts[1], parts.slice(2).join('/'));
    return r.error && r.error.startsWith('見つかりません') ? J(r, 404) : wrap(r);
  }
  return J({ error: '見つかりません。' }, 404);
}
