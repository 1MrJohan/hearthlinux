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
