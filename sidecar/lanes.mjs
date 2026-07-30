// Which workers a job may use.
//
// Foreground (the combat forecast) takes them from the START of the pool,
// background (the shop forecast) from the END, so the two never share one.
//
// That matters because SimClient's request lock holds one job in flight at a
// time *except* on cancellation: a shop forecast that yields to CombatStart
// keeps running in the sidecar while the real fight dispatches. server.mjs's
// watchdog assumes exclusivity — retireWorker terminates a worker and its
// `retired` flag then suppresses the exit handler — so a shared worker would
// cost the other job its shards with no error anywhere. Splitting the pool
// makes the assumption true by construction rather than by the lock alone.
//
// reserve = 0 asks for no split and gives both lanes the whole pool, which is
// the behaviour this replaced. A pool of one cannot reserve: the foreground
// keeps it, because the real fight is the one somebody is waiting on.
//
// Its own module so the arithmetic can be tested without importing server.mjs,
// which would spawn the pool and pay ~350MB for a card DB per worker.
export function lanes(pool, reserve) {
    const n = pool.length;
    const r = Math.max(0, Math.min(reserve | 0, n - 1));
    return { fg: pool.slice(0, n - r), bg: r ? pool.slice(n - r) : pool.slice(0) };
}

// Whether a lane still has an un-ready worker in it. simulate()'s readiness
// barrier used to await the whole pool (`poolReady()`), which meant a
// foreground job could block on a background worker's card-DB rebuild after
// a retirement — a CombatStart landing during that rebuild has no business
// waiting on a worker it will never dispatch to. Scoping the wait to the
// lane the job will actually use fixes that at the source; this is the pure
// predicate so it can be tested without spawning real workers.
export function notReady(lane) {
    return lane.some((w) => !w.ready);
}

// The pool-position mutation server.mjs's retireWorker/spawnWorker pair
// perform: remove `entry` and reinsert `replacement` at the vacated index
// (not the tail), so lanes() keeps splitting the pool in the same place
// across a retirement. Exported so the invariant can be tested without
// duplicating the rule, and without spawning a real worker pool.
export function retireAndReplace(pool, entry, replacement) {
    const i = pool.indexOf(entry);
    const next = pool.slice();
    if (i >= 0) next.splice(i, 1);
    next.splice(i >= 0 ? i : next.length, 0, replacement);
    return next;
}
