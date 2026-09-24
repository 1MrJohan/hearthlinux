# Reading the banner before the player clicks past it

## Problem

Game 412 finished 1st (6009 → 6109) and the rating was not read. GameState
ended the game at 18:33:19.6, but the win animation ran until 18:33:59.8, when
`EndGameScreen` shows the banner. The player had typed 6109 by hand by
18:34:23, so the banner was up for a few seconds at most, and the player
reports that clicking past it quickly always loses the read.

Once the number has stopped counting, the reader needs two consecutive reads
to agree:

- one `read_once` takes ~250ms at idle priority (~170ms at normal), measured
  on the 2nd-place fixture. That is two rounds of OCR: the label first, then
  four digit reads in parallel.
- `watch` sleeps 400ms between reads, even while a banner is on screen.

So it takes up to ~1.4s after the number settles, and the count-up comes
before that. A quick click beats it.

Whether a 1st-place banner reads at all is also unknown. None has been
captured.

## Decision

1. **One read is enough when it joins the chain.** A screen reading also
   states the rating *before* the game (`rating − delta`). When that equals
   the last stored rating, the read is accepted at once. A number caught
   mid-count, or misread, would have to be wrong in exactly the way that still
   subtracts to the previous rating, and nothing on the banner produces that.
   Without a previous reading, or when the chain does not join (untracked
   games), two consecutive agreeing reads are still required, as now.
2. **No sleep while a banner is located.** The 400ms interval exists to keep
   a screen with no banner cheap. Once `locate()` finds one, the next read
   starts immediately. A banner is on screen for seconds at most, so
   back-to-back reads cost nothing that matters.
3. **One round of OCR.** The label and the four digit reads run together.
   Reading the label first only saved work on lookalike screens, and
   `locate()` already screens those out on pixels.

After the number settles, that is ~250-500ms when the chain joins and ~250-750ms
when it does not.

4. **Keep the evidence of a miss.** When a watch ends having located a banner
   it could not read, the last such frame (the band and the label, as PPM) is
   written to `~/.cache/hs-bg-tracker/banners/`, and only the newest 10 pairs
   are kept. The unread 1st-place banner is exactly what this would have
   captured. The cache is disposable, and these are crops of the game window
   only.
5. **Log the timing.** When the banner was first located (relative to
   `GameEnd`) and when a read was accepted or given up, at INFO in the tracker
   log. The next quick click then says whether it was speed or layout.

`Pipeline` hands the reader the last stored rating through a new
`HistoryDB.last_rating()`.

## Verification

- `pytest tests/`: a chain-joining read is accepted without a second read;
  a non-joining read still needs agreement; no sleep between reads once a
  banner is located; a miss writes one band/label pair and prunes to 10; a
  watch with no banner writes nothing.
- The three fixtures still read exactly, and `read_once` time on the fixture
  drops from two OCR rounds to one.
- Live: the next game read before a quick click-through, or a saved frame
  showing why not.
