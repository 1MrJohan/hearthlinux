// One simulation worker: owns a card DB and runs a shard of the trials.
//
// Every worker holds its own AllCardsService (~350 MB resolved), which is why
// the pool is small and why the parent never loads one — it only routes.
//
// Messages in:  {jobId, input, sims}
// Messages out: {jobId, partial: RawResult} … then {jobId, result: RawResult}
//               {jobId, error: "message"}
//
// RawResult carries COUNTS and damage SUMS, never percentages or averages:
// those cannot be pooled across shards without the denominators.

import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { parentPort, workerData } from 'node:worker_threads';

import { checkBattleCompatibility, implementedCardIds } from './compatibility.mjs';

const require = createRequire(import.meta.url);
const { simulateBattle, assignCards } = require('@firestone-hs/simulate-bgs-battle');
const { CardsData } = require('@firestone-hs/simulate-bgs-battle/dist/cards/cards-data.js');
const { cardMappings } = require('@firestone-hs/simulate-bgs-battle/dist/cards/impl/_card-mappings.js');
const { AllCardsService } = require('@firestone-hs/reference-data');

const simulatorDist = join(dirname(
    require.resolve('@firestone-hs/simulate-bgs-battle/package.json'),
), 'dist');
const implementedCards = implementedCardIds(simulatorDist, Object.keys(cardMappings));

const cards = new AllCardsService();
cards.initializeCardsDbFromCards(JSON.parse(readFileSync(workerData.cardsFile, 'utf8')));
assignCards(cards);
const cardsData = new CardsData(cards);
cardsData.inititialize(); // [sic] — typo is in the upstream API

// Partials exist to put a number on screen fast, not to animate a counter.
// Anything tighter than this is churn the UI cannot use.
const PARTIAL_MIN_INTERVAL_MS = 120;

const raw = (r) => ({
    won: r.won, tied: r.tied, lost: r.lost,
    wonLethal: r.wonLethal, lostLethal: r.lostLethal,
    // Running totals, not averages — the parent divides once, at the end.
    damageWonSum: r.damageWon, damageLostSum: r.damageLost,
    damageWonRange: r.damageWonRange, damageLostRange: r.damageLostRange,
});

function run(jobId, shard, input, sims) {
    const compatibility = checkBattleCompatibility(input, cards, implementedCards);
    if (compatibility.missingData.length || compatibility.unsupported.length) {
        const parts = [];
        if (compatibility.missingData.length) {
            parts.push(`card data missing: ${compatibility.missingData.join(', ')}`);
        }
        if (compatibility.unsupported.length) {
            parts.push(`combat behavior unsupported: ${compatibility.unsupported.join(', ')}`);
        }
        parentPort.postMessage({
            jobId, shard,
            error: parts.join('; '),
            unsupportedCards: [
                ...compatibility.missingData, ...compatibility.unsupported,
            ],
        });
        return;
    }
    const battleInput = {
        ...input,
        options: {
            numberOfSimulations: sims,
            maxAcceptableDuration: 8000,
            skipInfoLogs: true,
            // The Spectator otherwise retains a full replay of every trial for
            // a UI that could show sample battles. Nothing here reads them, and
            // they cost a third of the throughput and over a gigabyte of heap
            // on a late-game board (measured 3039 -> 4695 sims/s).
            includeOutcomeSamples: false,
            hideMaxSimulationDurationWarning: true,
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
    let lastPost = 0;
    while (!step.done) {
        const now = Date.now();
        if (step.value && now - lastPost >= PARTIAL_MIN_INTERVAL_MS) {
            lastPost = now;
            parentPort.postMessage({ jobId, shard, partial: raw(step.value) });
        }
        step = gen.next();
    }
    parentPort.postMessage({ jobId, shard, result: raw(step.value) });
}

parentPort.on('message', ({ jobId, shard, input, sims }) => {
    try {
        run(jobId, shard, input, sims);
    } catch (e) {
        parentPort.postMessage({ jobId, shard, error: String(e?.stack ?? e) });
    }
});

parentPort.postMessage({ ready: true });
