// BG combat simulator sidecar.
//
// Protocol: one JSON object per line on stdin, one per line on stdout.
//   {"id": 1, "op": "ping"}                          -> {"id": 1, "result": "pong"}
//   {"id": 2, "op": "simulate", "input": BgsBattleInfo, "sims": 8000, "workers": 4}
//     -> {"id": 2, "partial": {...}}   (zero or more, as the run tightens)
//     -> {"id": 2, "result": {won, tied, lost, wonPercent, tiedPercent,
//                             lostPercent, averageDamageWon, averageDamageLost,
//                             damageWonRange, damageLostRange}}
// Errors: {"id": n, "error": "message"}. A "ready" line is printed on startup.
//
// The trials are sharded across worker threads, each with its own card DB.
// This process deliberately never builds one: it only routes and pools, so the
// ~350 MB a card DB resolves to is paid once per worker and not once extra here.

import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Worker } from 'node:worker_threads';
import { lanes } from './lanes.mjs';

const require = createRequire(import.meta.url);

const CACHE_DIR = process.env.XDG_CACHE_HOME
    ? join(process.env.XDG_CACHE_HOME, 'hs-bg-tracker')
    : join(homedir(), '.cache', 'hs-bg-tracker');
const CARDS_CACHE = join(CACHE_DIR, 'firestone-cards.json');
const CARDS_URLS = [
    'https://static.zerotoheroes.com/data/cards/cards_enUS.gz.json',
    'https://static.firestoneapp.com/data/cards/cards_enUS.gz.json',
];
const CACHE_MAX_AGE_MS = 7 * 24 * 3600 * 1000;

// Past four the gain flattens hard while memory keeps climbing: measured
// 9.2k -> 34k sims/s going 1 -> 4 workers (1.5 GB), but only 38k at 8 (3.1 GB).
const DEFAULT_WORKERS = 4;
const MAX_WORKERS = 8;

// Partials exist to put a number on screen fast, not to animate a counter.
const PARTIAL_MIN_INTERVAL_MS = 120;

/** Make sure the card cache is on disk and fresh; workers read it themselves. */
async function ensureCardsFile() {
    try {
        if (Date.now() - statSync(CARDS_CACHE).mtimeMs < CACHE_MAX_AGE_MS) {
            return CARDS_CACHE;
        }
    } catch {}
    let lastErr;
    for (const url of CARDS_URLS) {
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
            const text = await res.text();
            mkdirSync(CACHE_DIR, { recursive: true });
            writeFileSync(CARDS_CACHE, text);
            return CARDS_CACHE;
        } catch (e) {
            lastErr = e;
        }
    }
    // A stale cache beats no simulator at all.
    try {
        statSync(CARDS_CACHE);
        return CARDS_CACHE;
    } catch {}
    throw lastErr;
}

const cardsFile = await ensureCardsFile();
const WORKER_FILE = join(fileURLToPath(new URL('.', import.meta.url)), 'sim-worker.mjs');

const requestedWorkers = Number(process.env.BGTRACKER_SIM_WORKERS) || DEFAULT_WORKERS;
const workerCount = Math.max(1, Math.min(MAX_WORKERS, requestedWorkers));

const pool = [];
const inflight = new Map();   // jobId -> shard bookkeeping
let nextJobId = 0;

function spawnWorker(index) {
    // stdout/stderr are captured rather than inherited: this process speaks a
    // JSON-lines protocol on stdout, and one stray console.log from inside the
    // simulator would corrupt it. Drained immediately so nothing backs up.
    const worker = new Worker(WORKER_FILE, {
        workerData: { cardsFile },
        stdout: true,
        stderr: true,
    });
    worker.stdout.resume();
    worker.stderr.resume();
    const entry = { worker, index, ready: false };
    worker.on('message', (msg) => {
        if (msg.ready) {
            entry.ready = true;
            return;
        }
        onShardMessage(msg);
    });
    worker.on('error', (e) => { if (!entry.retired) failAllFor(entry, String(e?.stack ?? e)); });
    worker.on('exit', (code) => {
        if (!entry.retired && code !== 0) failAllFor(entry, `worker exited with code ${code}`);
    });
    // Inserted at `index` rather than pushed, so a mid-pool replacement (from
    // retireWorker, below) lands where the retired worker sat instead of at
    // the tail — pool *positions* are what lanes() splits on, so a replacement
    // that landed at the end would silently move the fg/bg boundary. The
    // initial construction loop calls this with 0..n-1 in order, where
    // splice(index, 0, entry) and push() are equivalent.
    pool.splice(index, 0, entry);
    return entry;
}

for (let i = 0; i < workerCount; i++) spawnWorker(i);

async function poolReady() {
    while (pool.some((w) => !w.ready)) {
        await new Promise((r) => setTimeout(r, 20));
    }
}

// ---- pooling ---------------------------------------------------------
// Sum counts and damage SUMS, then derive percentages once. Averaging the
// shards' percentages instead would weight a truncated shard as heavily as a
// full one and quietly bias the answer.

const ZERO = () => ({
    won: 0, tied: 0, lost: 0, wonLethal: 0, lostLethal: 0,
    damageWonSum: 0, damageLostSum: 0,
    damageWonRange: null, damageLostRange: null,
});

function addRange(acc, range, weight) {
    if (!range || !weight) return acc;
    if (!acc) return { min: range.min * weight, max: range.max * weight, w: weight };
    return { min: acc.min + range.min * weight, max: acc.max + range.max * weight, w: acc.w + weight };
}

function combine(shards) {
    const total = ZERO();
    let wonRange = null;
    let lostRange = null;
    for (const s of shards) {
        if (!s) continue;
        total.won += s.won; total.tied += s.tied; total.lost += s.lost;
        total.wonLethal += s.wonLethal; total.lostLethal += s.lostLethal;
        total.damageWonSum += s.damageWonSum; total.damageLostSum += s.damageLostSum;
        // The library clears its per-trial damage arrays before returning, so an
        // exact pooled percentile is not recoverable. A trial-weighted mean of
        // the shard bounds is the honest approximation.
        wonRange = addRange(wonRange, s.damageWonRange, s.won);
        lostRange = addRange(lostRange, s.damageLostRange, s.lost);
    }
    total.damageWonRange = wonRange ? { min: wonRange.min / wonRange.w, max: wonRange.max / wonRange.w } : null;
    total.damageLostRange = lostRange ? { min: lostRange.min / lostRange.w, max: lostRange.max / lostRange.w } : null;
    return total;
}

/** Mirrors the library's own rounding, including its never-quite-0/100 guard. */
function pct(n, total) {
    if (!total) return 0;
    const v = Math.round((10 * (100 * n)) / total) / 10;
    if (v === 0 && n !== 0) return 0.01;
    if (v === 100 && n !== total) return 99.9;
    return v;
}

function present(total) {
    const n = total.won + total.tied + total.lost;
    const wonPercent = pct(total.won, n);
    const lostPercent = pct(total.lost, n);
    return {
        won: total.won, tied: total.tied, lost: total.lost,
        wonPercent, lostPercent,
        // Taken from the other two so the three always sum to 100, as upstream does.
        tiedPercent: Math.max(0, Math.round(10 * (100 - lostPercent - wonPercent)) / 10),
        wonLethalPercent: pct(total.wonLethal, n),
        lostLethalPercent: pct(total.lostLethal, n),
        averageDamageWon: total.won ? total.damageWonSum / total.won : 0,
        averageDamageLost: total.lost ? total.damageLostSum / total.lost : 0,
        damageWonRange: total.damageWonRange,
        damageLostRange: total.damageLostRange,
    };
}

// ---- job dispatch ----------------------------------------------------

function onShardMessage(msg) {
    const job = inflight.get(msg.jobId);
    if (!job) return;
    if (msg.error) {
        clearTimeout(job.timer);
        inflight.delete(msg.jobId);
        out({ id: job.id, error: msg.error });
        return;
    }
    // Latest state per shard, keyed by shard index: a partial supersedes that
    // shard's previous partial, never another shard's. Summing arrivals instead
    // would double-count every trial a shard has already reported.
    if (msg.partial) {
        if (!job.done.has(msg.shard)) {
            job.shards[msg.shard] = msg.partial;
            maybeEmitPartial(job);
        }
        return;
    }
    job.shards[msg.shard] = msg.result;
    job.done.add(msg.shard);
    if (job.done.size === job.expected) {
        clearTimeout(job.timer);
        inflight.delete(msg.jobId);
        out({ id: job.id, result: present(combine(Object.values(job.shards))) });
    }
}

function maybeEmitPartial(job) {
    const now = Date.now();
    if (now - job.lastPartial < PARTIAL_MIN_INTERVAL_MS) return;
    job.lastPartial = now;
    const merged = combine(Object.values(job.shards));
    if (merged.won + merged.tied + merged.lost > 0) {
        out({ id: job.id, partial: present(merged) });
    }
}

// Known remaining hole: this fails EVERY inflight job, not just the one on
// `entry`. The lane split only closes the silent terminate()+`retired` path a
// stuck-worker retirement takes above — a genuine worker error or non-zero
// exit still takes down a concurrent job on the other lane with an explicit
// error, regardless of lane. Narrowing this needs a worker-crash test first;
// left as-is deliberately.
function failAllFor(entry, message) {
    for (const [jobId, job] of [...inflight]) {
        clearTimeout(job.timer);
        inflight.delete(jobId);
        out({ id: job.id, error: message });
    }
    retireWorker(entry);
}

// Pull a worker out of the pool and stand up a replacement. Idempotent via the
// `retired` flag, which also tells the worker's own exit/error handler that the
// death was deliberate — so terminating a hung worker does not cascade into
// failing the request about to run on its replacement.
function retireWorker(entry) {
    if (entry.retired) return;
    entry.retired = true;
    const i = pool.indexOf(entry);
    if (i >= 0) pool.splice(i, 1);
    entry.worker.terminate();
    // Reinsert the replacement at the vacated index, not the tail. Appending
    // would shift every worker after i left by one, moving the fg/bg lane
    // boundary out from under any job already dispatched against it — see the
    // comment on spawnWorker.
    if (pool.length < workerCount) spawnWorker(i >= 0 ? i : pool.length);
}

// A worker stuck in a non-terminating trial never messages, errors or exits, so
// the crash path above cannot see it. The watchdog is the backstop: answer the
// caller with whatever odds the finished shards gathered (a partial from 6000
// trials beats none), then retire the shards that never came back — terminate()
// is the only thing that stops a CPU-bound thread — so the next request lands on
// a healthy, respawned pool instead of hanging on the same stuck worker.
//
// Concurrent jobs are expected, not excluded: SimClient's request lock holds
// one job at a time except across a cancellation, where a shop forecast keeps
// running in the sidecar while the combat forecast it yielded to dispatches.
// What makes retiring a stuck worker here safe is lanes.mjs, not the lock —
// foreground and background jobs run on disjoint worker positions, so a
// worker this function retires and replaces can only belong to the job that
// owns it. That guarantee holds only while the pool has more than one worker;
// with a pool of one, lanes() clamps the reservation to zero and both lanes
// share the single worker, so a stuck job there still costs whatever else is
// in flight — there is no isolation left to lose.
function onJobTimeout(jobId) {
    const job = inflight.get(jobId);
    if (!job) return;
    inflight.delete(jobId);
    const merged = combine(Object.values(job.shards));
    if (merged.won + merged.tied + merged.lost > 0) {
        out({ id: job.id, result: present(merged) });
    } else {
        out({ id: job.id, error: 'simulation timed out' });
    }
    for (let i = 0; i < job.expected; i++) {
        if (!job.done.has(i) && job.workers[i]) retireWorker(job.workers[i]);
    }
}

async function simulate(id, input, sims, workers, deadline, background, reserve) {
    await poolReady();
    const total = sims ?? 8000;
    const split = lanes(pool, reserve);
    const lane = background ? split.bg : split.fg;
    const usable = Math.max(1, Math.min(lane.length, workers || lane.length));
    const shards = [];
    for (let i = 0; i < usable; i++) {
        // Spread the remainder so the shards differ by at most one trial.
        shards.push(Math.floor(total / usable) + (i < total % usable ? 1 : 0));
    }
    const jobId = ++nextJobId;
    const job = {
        id, expected: usable, shards: {}, done: new Set(), lastPartial: 0,
        // The worker entry each shard went to, so the watchdog can kill exactly
        // the ones that never report back.
        workers: lane.slice(0, usable), timer: null,
    };
    inflight.set(jobId, job);
    if (deadline) job.timer = setTimeout(() => onJobTimeout(jobId), deadline);
    shards.forEach((count, i) => {
        lane[i].worker.postMessage({ jobId, shard: i, input, sims: count });
    });
}

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

await poolReady();
out({
    ready: true,
    simulator: require('@firestone-hs/simulate-bgs-battle/package.json').version,
    workers: pool.length,
});

createInterface({ input: process.stdin }).on('line', (line) => {
    line = line.trim();
    if (!line) return;
    let msg;
    try {
        msg = JSON.parse(line);
    } catch (e) {
        out({ error: `bad json: ${e.message}` });
        return;
    }
    try {
        if (msg.op === 'ping') out({ id: msg.id, result: 'pong' });
        // simulate() is async and deliberately not awaited — the readline
        // handler must stay free to take the next line. That makes the catch
        // mandatory: an unhandled rejection (a postMessage to a worker retired
        // between poolReady() and dispatch, say) terminates the process, which
        // would take the sidecar down mid-game instead of failing one fight.
        else if (msg.op === 'simulate') {
            simulate(msg.id, msg.input, msg.sims, msg.workers, msg.deadline,
                     msg.background, msg.reserve)
                .catch((e) => out({ id: msg.id, error: String(e?.stack ?? e) }));
        }
        else out({ id: msg.id, error: `unknown op: ${msg.op}` });
    } catch (e) {
        out({ id: msg.id, error: String(e?.stack ?? e) });
    }
});
