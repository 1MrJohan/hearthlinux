# Handoff: Battlegrounds Overlay — Layout & UI Refresh ("Dark Oak")

## Overview
A visual refresh of the in-game Battlegrounds overlay HUD: a warm Hearthstone
"tavern" look (carved oak, gold trim, gem stats) replacing the current flat
dark-grey panels. Same information, same panels, same behavior — new skin plus
a few layout upgrades (turn medallion, win/tie/loss bar, ranked leaderboard
rail with hero portraits, card-art minion tiles).

The approved direction is **Dark Oak**. A lighter **Parchment** variant and a
faithful **Before (current)** recreation are also in the prototype for
reference/contrast — you only need to build Dark Oak unless we say otherwise.

## About the design files
The files in this bundle are **design references authored in HTML** (a Design
Component prototype), **not** production code to copy. The real overlay is
**Python + GTK4** (`bgtracker/overlay/`). The task is to **re-create this look
in the existing GTK4 widgets** using the app's established patterns
(`Gtk.CssProvider`, `Gtk.Fixed` canvas, `Gtk.Picture`, the `set_*` update API)
— not to ship HTML. Every measurement below is given so you can implement from
this README alone.

## Fidelity
**High-fidelity.** Colors, typography, spacing, radii, and shadows are final.
Match them. The HTML uses per-element inline styles (prototype constraint);
in GTK these become CSS classes on the existing panels.

---

## Where this maps in the current code
| Design piece | Current code |
|---|---|
| Panel frame / skin | `CSS_TEMPLATE` + `.hud` class in `overlay/window.py` |
| HUD (status/odds/damage) | `OverlayWindow.status/odds/damage`, `set_status/set_odds/set_damage` |
| Turn medallion | **new** — add a label; feed from the turn number already in status |
| Odds bar | **new** — replaces/augments the `.odds` markup label |
| Minion tile | `MinionCard` in `overlay/widgets.py` (currently full-card `Gtk.Picture`) |
| Enemy / next / hover boards | `BoardPanel` in `overlay/widgets.py` |
| Leaderboard rail + hero orbs | **new panel** — see `hover.py` for the portrait-hover hookup that already exists |
| Edit / Lock layout | `_make_panel`, `_on_lock`, `.editing`, `.grip`, `.lockbtn` (keep as-is) |
| Card/hero art | `data/art.py` — `RENDER_URL`, `ARTCROP_URL`, `TILE_URL` already defined |

**Keep unchanged:** layer-shell setup, `Gtk.Fixed` absolute positioning, drag
gestures, input-region click-through, config persistence, and the entire
`set_*` update API. This is a **view/CSS** change, not a logic change.

---

## Design tokens

### Fonts
- **Display / numbers / titles:** `Cinzel` (700–800). Titles are UPPERCASE,
  letter-spacing ~1.5px. Use for panel titles, turn number, odds %, stat gems,
  rank badges, the Lock button.
- **Body / labels:** `Alegreya Sans` (400–800). Minion names, HP, buff chips,
  status text. (Current build uses Cantarell — switch to Alegreya Sans, or the
  nearest bundled serif-humanist if you don't want to ship a font.)

### Dark Oak palette (build this)
| Token | Value | Use |
|---|---|---|
| Panel background | `linear-gradient(158deg, rgba(42,30,18,.94), rgba(20,13,7,.96))` | all panel fills |
| Panel border | `1px solid rgba(212,175,55,.5)` | panel edge |
| Panel shadow | `0 12px 34px rgba(0,0,0,.55)`, inset `0 1px 0 rgba(255,255,255,.06)` | panel lift |
| Panel radius | `12px` | panels; `10px` inner chips |
| Panel padding | `11px 13px` | panel content |
| Ink (primary text) | `#f1e4c8` | body text |
| Dim (secondary) | `#b39a6e` | captions, meta |
| Gold (accent) | `#f4d47a` | titles, YOU, values |
| Rule (divider) | `rgba(212,175,55,.28)` | hairlines |
| Win | `#7fca57` · bar `#8ed36a→#57a038` | odds |
| Tie | `#e6b846` · bar `#f0c85a→#cf9a1f` | odds |
| Loss | `#e37a5c` · bar `#dd6f52→#a83220` | odds |

### Parchment variant (reference only)
Panel `linear-gradient(180deg,#f4e9cd,#e3d2a8)`, border `2px solid #b8935a`,
ink `#41301a`, dim `#7c6541`, gold `#8a5f1c`, title `#6e4a17`,
win `#3f7d2a` / tie `#946213` / loss `#a5301c`.

### Stat gems (minion tiles)
- **Attack gem:** `radial-gradient(circle at 40% 32%, #ffe6a6, #e0a53a 55%, #a9701c)`
- **Health gem:** `radial-gradient(circle at 40% 32%, #ff9e84, #d5462c 55%, #8f2317)`
- 25px circle, `1.5px solid rgba(255,255,255,.35)`, Cinzel 800 white with
  `text-shadow: 0 1px 2px rgba(0,0,0,.85)`. Attack bottom-left, health bottom-right.

### Golden minion treatment
Border becomes a gradient `linear-gradient(140deg,#fff2b0,#d4af37 45%,#8a6410)`
plus glow `0 0 14px rgba(246,210,120,.75)` and inset `0 0 12px rgba(246,210,120,.35)`.

### Keyword pips
13px rounded squares, dark glyph on tribe/keyword color:
Taunt `#9bb0c3`, Divine Shield `#ffd873`, Poison `#74c957`, Venomous `#46b98a`,
Windfury `#86c9ef`, Reborn `#d78cf0`. One pip per keyword under the name.

---

## Panels (geometry from the 1180×707 prototype stage; scale to monitor as today)

All panels keep the existing `Gtk.Fixed` + config-saved positions. Positions
below are the prototype's; the current `defaults{}` in `window.py` are fine to
keep — only the **styling** changes.

### 1. HUD (top-right) — the hero panel
Width ~300px. Structure top→bottom:
1. **Title row:** left = phase title in Cinzel gold uppercase (`COMBAT FORECAST`
   / `RECRUIT PHASE` / `HERO SELECT`); right = meta in dim (`18 HP · vs Tickatus`
   or `18 HP · Tavern 3`).
2. **Hairline** (rule color).
3. **Body row (combat/shop):**
   - **Turn medallion** — 58px circle, `radial-gradient(circle at 50% 34%, #2a1d10, #120c06)`,
     `2px solid #d4af37`, big Cinzel 800 `#f4d47a` number, tiny `TURN` caption.
     In combat it gently pulses (box-shadow 10px→20px gold, 2.4s ease-in-out).
   - **Odds block** — three centered columns `WIN / TIE / LOSS` (Cinzel 800,
     colored per token, 21px) with tiny dim captions, then a **9px bar** split
     win/tie/loss using the bar gradients, `1px` gold-ish border, inset shadow.
   - **Damage pills** — two rounded pills: `▲ 14 dealt` (win-tinted) and
     `▼ 9 taken` (loss-tinted).
4. **Hero Select** shows just a status line ("Waiting — choose your hero"), no
   odds/medallion.

Wire to existing API: `set_status` → phase title + meta; `set_odds(win,tie,loss)`
→ the three numbers + bar widths; `set_damage(dealt,taken)` → the pills. Add a
`set_turn(n)` (or parse from status) for the medallion.

### 2. Enemy board (top-center)
Header row: Cinzel gold `ENEMY BOARD` + dim meta (`Tickatus · 27 HP · Tavern 5`).
Then a horizontal row of **minion tiles** (see below). This is `BoardPanel` +
`MinionCard` restyled. Max width ~640px, centered.

### 3. Tavern Buffs (upper-left-center)
Width ~176px. Title `TAVERN BUFFS`. Each buff = a chip row: colored dot +
label (ink) + value in Cinzel gold (`+2/+2`). Pool spells listed under as
`✦ Name` in a cool accent (`#8fd0e6`). Maps to `set_buffs(entries, spells)`.

### 4. Leaderboard rail (far left) — NEW
Vertical column, ~98px wide, its own oak sub-frame
(`linear-gradient(160deg,rgba(24,16,9,.72),rgba(14,9,5,.78))`, `1px` gold-30 border,
radius 11). 8 rows, each:
- **Hero orb** — 40px circle filled with the hero's **art crop** (`ARTCROP_URL`),
  `object-position: center 26%`, circular clip. Ring: `2px` dark, or `2px #f4d47a`
  + gold glow for YOU. Dead = grayscale + 50% opacity.
- **Rank badge** — small pill bottom-left of the orb, Cinzel 800; gold gradient
  for YOU, else dark `rgba(12,8,4,.92)` with cream text.
- **HP** — right-aligned, Cinzel-ish; gold for YOU, red strike-through if dead.
Hovering a row opens the scout popout (#5). The pointer→row hookup already
exists for the in-game leaderboard in `overlay/hover.py`.

### 5. Scout popout (hover) — restyle of the hover board
Anchored right of the rail, with a small left-pointing notch. Header: hero orb
(art) + hero name (Cinzel gold) + dim status (`last seen · turn 6` /
`not scouted yet` / `eliminated`). Body: that player's last-seen board as
minion tiles. Maps to `set_hover_board(title, board)` / `clear_hover_board`.

---

## Minion tile (redesign of `MinionCard`, `overlay/widgets.py`)
Current: a `Gtk.Picture` of the full rendered card + a stats label. New Dark
Oak tile (80px wide):
- Oak tile background + border + radius (tokens above); golden variant when applicable.
- **Circular portrait, 52px** — the **art crop** (`ARTCROP_URL`, square, no frame),
  scaled up ~110–150% and shifted up (`object-position: center 32%`) so the
  character's face fills the circle. Use `Gtk.Overlay`: base = the picture
  (rounded via CSS `border-radius:50%; overflow:hidden` or a clip), overlaid
  with the two stat gems.
- **Attack/Health gems** — bottom-left / bottom-right of the portrait (tokens above).
  Feed from the **live/buffed** `minion.attack` / `minion.health` (as today —
  printed stats are wrong in BG).
- **Name** — Alegreya Sans, ink, 2-line clamp, centered, 10px.
- **Keyword pips** — from `minion.flags`.

> The current `MinionCard` uses the full-card **render** (`RENDER_URL`) and
> `art.fetch_card`. For the new portrait you want the **art crop**
> (`ARTCROP_URL`, already defined in `art.py`). Add a `fetch_artcrop(card_id)`
> parallel to `fetch_card` (same executor/caching pattern, writing under
> `art/crops/`), or generalize `fetch_card` with a `kind=` arg. Keep the
> render path for the "Before" skin if you keep that skin.

The **"Before (current)"** skin in the prototype simply shows the full card
**render** in the tile (what `MinionCard` does today) — no work needed there;
it exists only to contrast against Dark Oak.

---

## CSS starting point (replace `CSS_TEMPLATE` in `window.py`)
Keep the `{pad}`, `{odds_px}`, etc. scale substitutions. Sketch:
```css
window { background: transparent; }
.hud {
    background-image: linear-gradient(158deg, rgba(42,30,18,.94), rgba(20,13,7,.96));
    border: 1px solid rgba(212,175,55,.5);
    border-radius: 12px;
    color: #f1e4c8;
    padding: {pad}px {pad2}px;
    /* GTK4 has no box-shadow on arbitrary widgets via CSS the same way;
       approximate the lift with border + a darker parent, or a Gtk.Frame. */
}
.hud .title { color:#f4d47a; font-family:'Cinzel'; font-weight:800; letter-spacing:1.5px; }
.hud .dim   { color:#b39a6e; font-size:{dim_px}px; }
.hud .line  { font-size:{line_px}px; }
.win  { color:#7fca57; } .tie { color:#e6b846; } .loss { color:#e37a5c; }
.grip { color:#f4d47a; font-weight:bold; }              /* was #7fd0ff */
.editing { border:2px dashed rgba(244,212,122,.9); }    /* was blue */
.lockbtn {
    background-image: linear-gradient(180deg,#f6e07f,#d4af37 55%,#a97e1f);
    color:#3a2708; border-radius:9px; padding:8px 18px; font-weight:800;
}
```
Notes for GTK4 specifics:
- The gems, orbs, medallion, and odds bar are best done as small `Gtk.Box`es
  with their own CSS classes + `Gtk.Overlay` for the gem-on-portrait stacking,
  rather than drawing. Rounded images: put the `Gtk.Picture` in a box with
  `border-radius` and `overflow: hidden` (GTK4 clips rounded).
- The bar is two/three `Gtk.Box`es in a horizontal `Gtk.Box`, widths set from
  the odds (`hexpand` with fixed fractions, or `set_size_request`).
- Pulse on the medallion: a CSS `@keyframes`-style animation isn't available;
  use a short `GLib.timeout`/`Gtk.CssProvider` swap, or just a static gold glow
  border if you want to skip animation.

---

## Verified card & hero art IDs (used in the prototype)
All confirmed to resolve on the HearthstoneJSON CDN.

**Heroes** (`ARTCROP_URL` with these ids):
`TB_BaconShop_HERO_34` Patchwerk · `_52` Deathwing · `_43` Dinotamer Brann ·
`_94` Tickatus · `_16` A. F. Kay · `_56` Alexstrasza · `_23` Shudderwock ·
`_67` Captain Hooktusk.

**Minions:** Demons — `LOOT_013` Vulgar Homunculus, `BRM_006` Imp Gang Boss,
`CS2_065` Voidwalker, `GVG_021` Voidcaller, `LOOT_368` Voidlord, `OG_122`
Mal'Ganis. Mechs — `EX1_556` Harvest Golem, `BOT_312` Security Rover,
`GVG_106` Junkbot, `GVG_113` Foe Reaper 4000. Dragons — `AT_072` Cobalt
Scalebane, `LOOT_357` Twilight Emissary, `DAL_735` Kalecgos, `EX1_561`
Alexstrasza, `NEW1_030` Deathwing. Beasts — `CFM_315` Alleycat, `EX1_531`
Scavenging Hyena, `LOE_050` Cave Hydra.

> These are just the prototype's sample lineup. In production the ids come from
> the live game state (`Minion.card_id`, `PlayerBoard.hero_card_id`) exactly as
> today — the tables above are only so you can reproduce the mock. Card art is
> Blizzard's copyright (personal-use), fetched/cached via `data/art.py`.

## Interactions & behavior
- **Skin** is a design choice, not a runtime toggle — build Dark Oak. (The
  prototype's Skin/Phase switches and the leaderboard hover-to-scout are just
  to demo states; live behavior is driven by the parser as today.)
- **Edit / Lock layout:** unchanged. Grips (`⠿ Title`) show in edit mode,
  dashed border via `.editing`, drag saves `pos_{name}_x/y`, the gold Lock
  button flips `overlay_edit` off live (`_on_lock`).
- **Panel visibility:** unchanged (`_set_content` — HUD always on; board/buffs/
  hover/next show when they have content or in edit mode).

## Files in this bundle
- `BG Overlay Redesign.dc.html` — the main prototype (all panels, 3 skins, phase/panel/edit switches).
- `MinionTile.dc.html` — the minion-tile component (portrait + gems + name + pips; art & golden support).
- `support.js` — runtime for the HTML prototype (so it opens in a browser; not needed for GTK).
- `tavern-bg.png` — the gameplay backdrop used to composite the mock (context only).

To view the prototype: open `BG Overlay Redesign.dc.html` in a browser
(needs internet for the fonts + card art).
