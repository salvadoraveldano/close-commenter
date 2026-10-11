// Hedera Testnet sidecar for Close Commenter. TESTNET ONLY. stdout = one JSON object, logs -> stderr.
// Usage: op run --env-file=.env.op -- node chain.mjs init|publish|fetch|ping [flags]
import fs from "node:fs";
import net from "node:net";
import zlib from "node:zlib";
import crypto from "node:crypto";
import { parseArgs } from "node:util";
import { Client, AccountId, PrivateKey, TopicCreateTransaction, TopicMessageSubmitTransaction } from "@hiero-ledger/sdk";

const MIRROR = "https://testnet.mirrornode.hedera.com";
const here = (f) => new URL(f, import.meta.url).pathname;
const CHAIN = here("chain.json"), KEYFILE = here(".close.key");
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const log = (...a) => console.error("[chain]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (msg) => { throw new Error(msg); };
// 0.0.90@1791735934.384000384 -> 0.0.90-1791735934-384000384 (HashScan + Mirror Node accept this form)
const txUrlId = (id) => String(id).replace("@", "-").replace(/\.(\d+)$/, "-$1");
const txUrl = (id) => `https://hashscan.io/testnet/transaction/${txUrlId(id)}`;
const topicUrl = (t) => `https://hashscan.io/testnet/topic/${t}`;

async function mirror(path) {
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(MIRROR + path);
      if (r.ok) return r.json();
      if (r.status !== 404 && r.status < 500 && r.status !== 429) fail(`mirror ${r.status} ${path}`);
      if (i >= 4) fail(`mirror ${r.status} ${path}`);
    } catch (e) { if (i >= 4) throw e; }
    await sleep(2000);
  }
}

// Key may be DER or raw hex, ECDSA or ED25519. Raw hex parses as both, so pick the parse whose public key
// matches the account's key on the Mirror Node. Reports the method + type, never the key.
async function loadOperator() {
  const id = process.env.HEDERA_ACCOUNT_ID, raw = process.env.HEDERA_PRIVATE_KEY;
  if (!id || !raw) fail("HEDERA_ACCOUNT_ID / HEDERA_PRIVATE_KEY not set (run via op run --env-file=.env.op)");
  if ((process.env.HEDERA_NETWORK || "testnet") !== "testnet") fail("testnet only");
  const acct = await mirror(`/api/v1/accounts/${id}`);
  const onChain = String(acct.key?.key || "").toLowerCase();
  const tries = [["fromStringDer", "DER"], ["fromStringECDSA", "ECDSA"], ["fromStringED25519", "ED25519"]], ok = [];
  for (const [fn, label] of tries) {
    try { const k = PrivateKey[fn](raw); ok.push({ k, method: label, type: k.type, match: k.publicKey.toStringRaw().toLowerCase() === onChain }); } catch {}
  }
  const hit = ok.find((o) => o.match) || fail(ok.length ? "private key does not match the account's public key" : "private key not parseable as DER/ECDSA/ED25519");
  return { id, key: hit.k, method: hit.method, type: hit.type, acct, balance_hbar: acct.balance.balance / 1e8 };
}

async function client() {
  const op = await loadOperator();
  const c = Client.forTestnet(); // hard-coded: never any other network
  c.setOperator(AccountId.fromString(op.id), op.key);
  log(`operator ${op.id} key=${op.method}/${op.type}`);
  return { c, op };
}

const readChain = () => { try { return JSON.parse(fs.readFileSync(CHAIN, "utf8")); } catch { return {}; } };
const closeKey = () => Buffer.from(fs.readFileSync(KEYFILE, "utf8").trim(), "hex");

// ---- init ----
async function init(a) {
  const cur = readChain();
  if (cur.network && cur.network !== "testnet") fail("chain.json network is not testnet");
  if (!fs.existsSync(KEYFILE)) {
    // a new key for an existing topic would make every earlier close undecryptable: refuse unless the caller starts over (--force)
    if (cur.topic_id && !a.force) fail("chain.json has a topic but .close.key is missing: restore the key from your backup, or run init --force for a new topic");
    fs.writeFileSync(KEYFILE, crypto.randomBytes(32).toString("hex") + "\n", { mode: 0o600 });
  }
  if (cur.topic_id && !a.force) return { topic_id: cur.topic_id, hashscan: topicUrl(cur.topic_id), existed: true };
  const { c, op } = await client();
  try {
    const tx = await new TopicCreateTransaction().setSubmitKey(op.key.publicKey).setTopicMemo("Northgate Labs month-end closes (synthetic demo)").execute(c);
    const topic_id = (await tx.getReceipt(c)).topicId.toString();
    fs.writeFileSync(CHAIN, JSON.stringify({ network: "testnet", topic_id }) + "\n");
    return { topic_id, hashscan: topicUrl(topic_id), key_type: op.type, key_parse: op.method };
  } finally { c.close(); }
}

// ---- publish ----
async function publish(a) {
  const t0 = Date.now();
  for (const k of ["package", "close-id", "period", "hash", "closed-by", "out"]) if (!a[k]) fail(`--${k} required`);
  const topic_id = readChain().topic_id || fail("no topic: run init first");
  const plain = fs.readFileSync(a.package);
  if (sha(plain) !== a.hash) fail("sha256 of package file != --hash");
  const hashOnly = !!a["hash-only"], close_id = a["close-id"];
  let enc = null;
  if (!hashOnly) {
    const iv = crypto.randomBytes(12), ci = crypto.createCipheriv("aes-256-gcm", closeKey(), iv);
    const ct = Buffer.concat([ci.update(zlib.gzipSync(plain, { level: 9 })), ci.final()]);
    enc = Buffer.concat([iv, ci.getAuthTag(), ct]); // iv|tag|ciphertext
  }
  const nChunks = enc ? Math.ceil(enc.length / 1024) : 0;
  if (nChunks > 100) fail(`payload needs ${nChunks} chunks (max 100): use --hash-only`);
  const begin = { t: "begin", v: 1, close_id, period: a.period, sha256: a.hash, prev: a.prev || "", closed_by: a["closed-by"], bytes: plain.length, enc: "aes-256-gcm+gzip", tier: hashOnly ? "hash-only" : "full" };
  const messages = 2 + nChunks; // begin + chunks + commit
  const commit = { t: "commit", close_id, messages };
  const { c } = await client();
  try {
    const send = async (msg, chunks) => {
      const t = new TopicMessageSubmitTransaction().setTopicId(topic_id).setMessage(msg);
      if (chunks) t.setMaxChunks(100);
      const rs = await t.executeAll(c); // SDK auto-chunks at 1024 bytes
      return Promise.all(rs.map(async (r) => ({ id: r.transactionId.toString(), seq: Number((await r.getReceipt(c)).topicSequenceNumber) })));
    };
    // each logical message waits for its receipt so on-chain order is begin < data < commit
    const b = await send(JSON.stringify(begin));
    const d = enc ? await send(enc, true) : [];
    const k = await send(JSON.stringify(commit));
    const all = [...b, ...d, ...k];
    if (all.length !== messages) log(`warning: ${all.length} messages sent, expected ${messages}`);
    const out = {
      topic_id, close_id, messages: all.length, bytes_encrypted: enc ? enc.length : 0, tx_ids: all.map((m) => m.id),
      links: [{ label: "Begin transaction", url: txUrl(b[0].id) }, { label: "Commit transaction", url: txUrl(k[0].id) }],
      sequence_range: [all[0].seq, all[all.length - 1].seq], ms: Date.now() - t0,
    };
    fs.writeFileSync(a.out, JSON.stringify(out, null, 1));
    return out;
  } finally { c.close(); }
}

// ---- fetch (Mirror Node REST only) ----
const gcmOpen = (blob) => {
  const d = crypto.createDecipheriv("aes-256-gcm", closeKey(), blob.subarray(0, 12));
  d.setAuthTag(blob.subarray(12, 28));
  return zlib.gunzipSync(Buffer.concat([d.update(blob.subarray(28)), d.final()]));
};

// ponytail: re-reads the whole topic on every fetch (100/page); fine for tens of closes, add ?sequencenumber=gte: paging past ~10k messages
async function readTopic(topic) {
  const msgs = [];
  let path = `/api/v1/topics/${topic}/messages?limit=100&order=asc`;
  while (path) { const j = await mirror(path); msgs.push(...j.messages); path = j.links?.next || null; }
  return msgs.map((m) => {
    const buf = Buffer.from(m.message, "base64"); let json = null;
    if (buf[0] === 0x7b) try { const j = JSON.parse(buf.toString("utf8")); if (j && j.t) json = j; } catch {}
    const ci = m.chunk_info, i = ci?.initial_transaction_id;
    return { seq: m.sequence_number, buf, json, gid: ci && ci.total > 1 && i ? `${i.account_id}@${i.transaction_valid_start}` : `seq${m.sequence_number}`, num: ci?.number || 1, total: ci?.total || 1 };
  });
}

// Latest complete begin..commit pair for close_id, or null while the mirror node still lags.
function findClose(msgs, id) {
  const ev = msgs.filter((m) => m.json && m.json.close_id === id && (m.json.t === "begin" || m.json.t === "commit"));
  for (let i = ev.length - 1; i > 0; i--) if (ev[i].json.t === "commit" && ev[i - 1].json.t === "begin") return { begin: ev[i - 1], commit: ev[i] };
  return null;
}

async function fetchClose(a) {
  const t0 = Date.now();
  for (const k of ["topic", "close-id", "out"]) if (!a[k]) fail(`--${k} required`);
  let msgs, f;
  for (let i = 0; i < 6; i++) { // mirror node lags consensus by 3-6 s
    msgs = await readTopic(a.topic); f = findClose(msgs, a["close-id"]);
    if (f) break;
    log(`close ${a["close-id"]} not complete yet, retry ${i + 1}`); await sleep(3000);
  }
  if (!f) fail(`no complete begin+commit for close_id ${a["close-id"]} on ${a.topic}`);
  const { begin, commit } = f, b = begin.json;
  const res = { sha256: b.sha256, begin: b, commit: true, messages: 2, ms: 0 };
  const groups = {}; // data candidates strictly between begin and commit, grouped by initial tx id
  for (const m of msgs) if (m.seq > begin.seq && m.seq < commit.seq && !m.json) (groups[m.gid] ||= []).push(m);
  if (b.tier === "hash-only") return { ok: true, ...res, matches_begin: null, payload: false, ms: Date.now() - t0 };
  let last = null;
  for (const g of Object.values(groups)) {
    if (g.length !== g[0].total) continue; // incomplete group
    let plain; try { plain = gcmOpen(Buffer.concat(g.sort((x, y) => x.num - y.num).map((m) => m.buf))); } catch { continue; }
    last = { plain, n: g.length };
    if (sha(plain) === b.sha256) break;
    last.bad = true;
  }
  if (!last) fail(fs.existsSync(KEYFILE) ? "no decryptable data message between begin and commit (wrong encryption key, or damaged data)" : "encryption key not found: .close.key is missing, so the payload cannot be decrypted (the hash can still be checked)");
  const good = !last.bad && sha(last.plain) === b.sha256;
  if (good) fs.writeFileSync(a.out, last.plain);
  return { ok: good, ...res, sha256: sha(last.plain), matches_begin: good, payload: true, messages: 2 + last.n, ms: Date.now() - t0, ...(good ? {} : { error: "sha256 mismatch" }) };
}

// ---- ping ----
const tcp = (host, port) => new Promise((res) => {
  const s = net.connect({ host, port, timeout: 3000 });
  s.on("connect", () => { s.destroy(); res(true); }).on("timeout", () => { s.destroy(); res(false); }).on("error", () => res(false));
});

async function ping() {
  const op = await loadOperator();
  const c = Client.forTestnet(), hosts = [...new Set(Object.keys(c.network).map((k) => k.split(":")[0]))].slice(0, 2);
  c.close();
  const grpc = {};
  for (const h of hosts) for (const p of [50211, 50212]) grpc[`${h}:${p}`] = await tcp(h, p);
  return { account: op.id, balance_hbar: op.balance_hbar, key_parse: op.method, key_type: op.type, grpc, grpc_ok: Object.values(grpc).some(Boolean) };
}

// ---- main ----
const cmds = { init, publish, fetch: fetchClose, ping };
const { values, positionals } = parseArgs({ allowPositionals: true, options: {
  force: { type: "boolean" }, "hash-only": { type: "boolean" }, package: { type: "string" }, "close-id": { type: "string" },
  period: { type: "string" }, hash: { type: "string" }, prev: { type: "string" }, "closed-by": { type: "string" },
  out: { type: "string" }, topic: { type: "string" } } });
let out, code = 0;
try { out = await (cmds[positionals[0]] || (() => fail("usage: init | publish | fetch | ping")))(values); if (out.ok === false) code = 1; }
catch (e) { out = { error: String(e.message || e) }; code = 1; }
process.stdout.write(JSON.stringify(out) + "\n", () => process.exit(code));
