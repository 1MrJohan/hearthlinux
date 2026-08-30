// Decide whether the exact Firestone package + card-data pair can honestly
// simulate an input. Pure helpers live here so the policy is unit-testable
// without starting the ~1.5GB worker pool.

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';


// Firestone has two implementation paths: its cardMappings registry and
// older switch tables compiled with literal card IDs. Scanning only .js (not
// source maps or the reference-data dependency) captures both without
// mistaking every known Hearthstone card for implemented behavior.
export function implementedCardIds(distDir, mappedIds = []) {
    const result = new Set(mappedIds);
    const pending = [distDir];
    while (pending.length) {
        const directory = pending.pop();
        for (const entry of readdirSync(directory, { withFileTypes: true })) {
            const path = join(directory, entry.name);
            if (entry.isDirectory()) {
                pending.push(path);
                continue;
            }
            if (!entry.name.endsWith('.js')) continue;
            const source = readFileSync(path, 'utf8');
            for (const match of source.matchAll(/["']([A-Za-z0-9]+(?:_[A-Za-z0-9]+)+)["']/g)) {
                result.add(match[1]);
            }
        }
    }
    return result;
}

function inputEntities(input) {
    const result = [];
    for (const side of [input?.playerBoard, input?.opponentBoard]) {
        if (!side) continue;
        const minions = [...(side.board ?? []), ...(side.player?.hand ?? [])];
        result.push(
            ...minions,
            ...(side.player?.trinkets ?? []),
            ...(side.player?.secrets ?? []),
        );
        for (const minion of minions) result.push(...(minion.enchantments ?? []));
    }
    return result;
}

export function checkBattleCompatibility(input, allCards, implementedCards) {
    const missingData = new Set();
    const unsupported = new Set();
    const combatCards = new Set(input?.trackerCombatCardIds ?? []);
    for (const entity of inputEntities(input)) {
        const cardId = entity?.cardId;
        if (!cardId) continue;
        const implemented = implementedCards.has(cardId);
        const card = allCards.getCard(cardId);
        // A stat-only unknown ID can still use live ATK/HEALTH. Implemented
        // behavior needs metadata for tribes, premium variants and summoned
        // IDs; conversely, a tracker-marked combat effect needs actual package
        // code, whether registered in cardMappings or a legacy switch table.
        if (!card?.id && implemented) {
            missingData.add(cardId);
        }
        if (combatCards.has(cardId) && !implemented) unsupported.add(cardId);
    }
    return {
        missingData: [...missingData].sort(),
        unsupported: [...unsupported].sort(),
    };
}

export function cacheIsFresh(cacheStat, packageStat, now = Date.now(), maxAgeMs) {
    return now - cacheStat.mtimeMs < maxAgeMs && cacheStat.mtimeMs >= packageStat.mtimeMs;
}
