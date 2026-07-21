// BG combat simulator sidecar.
//
// Protocol: one JSON object per line on stdin, one per line on stdout.
//   {"id": 1, "op": "ping"}                          -> {"id": 1, "result": "pong"}
//   {"id": 2, "op": "simulate", "input": BgsBattleInfo, "sims": 8000}
//     -> {"id": 2, "result": {won, tied, lost, wonPercent, tiedPercent,
//                             lostPercent, averageDamageWon, averageDamageLost}}
// Errors: {"id": n, "error": "message"}. A "ready" line is printed on startup.

import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { homedir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { simulateBattle, assignCards } = require('@firestone-hs/simulate-bgs-battle');
const { CardsData } = require('@firestone-hs/simulate-bgs-battle/dist/cards/cards-data.js');
const { AllCardsService } = require('@firestone-hs/reference-data');

const CACHE_DIR = process.env.XDG_CACHE_HOME
    ? join(process.env.XDG_CACHE_HOME, 'hs-bg-tracker')
    : join(homedir(), '.cache', 'hs-bg-tracker');
const CARDS_CACHE = join(CACHE_DIR, 'firestone-cards.json');
const CARDS_URLS = [
    'https://static.zerotoheroes.com/data/cards/cards_enUS.gz.json',
    'https://static.firestoneapp.com/data/cards/cards_enUS.gz.json',
];
const CACHE_MAX_AGE_MS = 7 * 24 * 3600 * 1000;

async function loadCardsJson() {
    try {
        if (Date.now() - statSync(CARDS_CACHE).mtimeMs < CACHE_MAX_AGE_MS) {
            return JSON.parse(readFileSync(CARDS_CACHE, 'utf8'));
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
            return JSON.parse(text);
        } catch (e) {
            lastErr = e;
        }
    }
    // fall back to a stale cache rather than dying
    try {
        return JSON.parse(readFileSync(CARDS_CACHE, 'utf8'));
    } catch {}
    throw lastErr;
}

const cards = new AllCardsService();
cards.initializeCardsDbFromCards(await loadCardsJson());
assignCards(cards);
const cardsData = new CardsData(cards);
cardsData.inititialize(); // [sic] — typo is in the upstream API

function runSimulation(input, sims) {
    const battleInput = {
        ...input,
        options: {
            numberOfSimulations: sims ?? 8000,
            maxAcceptableDuration: 8000,
            skipInfoLogs: true,
            ...input.options,
        },
    };
    const tribes = battleInput.gameState?.validTribes;
    const anomalies = battleInput.gameState?.anomalies;
    if (tribes?.length || anomalies?.length) {
        cardsData.inititialize(tribes, anomalies);
    }
    const gen = simulateBattle(battleInput, cards, cardsData);
    let step = gen.next();
    while (!step.done) step = gen.next();
    const r = step.value;
    return {
        won: r.won, tied: r.tied, lost: r.lost,
        wonPercent: r.wonPercent, tiedPercent: r.tiedPercent, lostPercent: r.lostPercent,
        wonLethalPercent: r.wonLethalPercent, lostLethalPercent: r.lostLethalPercent,
        averageDamageWon: r.averageDamageWon, averageDamageLost: r.averageDamageLost,
    };
}

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
out({ ready: true, simulator: require('@firestone-hs/simulate-bgs-battle/package.json').version });

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
        else if (msg.op === 'simulate') out({ id: msg.id, result: runSimulation(msg.input, msg.sims) });
        else out({ id: msg.id, error: `unknown op: ${msg.op}` });
    } catch (e) {
        out({ id: msg.id, error: String(e?.stack ?? e) });
    }
});
