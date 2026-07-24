"""Dark Oak theme: design tokens, bundled fonts, and the scaled stylesheet.

The overlay skin is a carved-oak/gold-trim Hearthstone "tavern" look. Every
colour, radius and size here comes from the approved design handoff
(`design_handoff_overlay_redesign/README.md`); treat those values as final.

Two things are worth knowing before editing:

* **Fonts are bundled, not installed.** Cinzel and Alegreya Sans ship as OFL
  `.ttf` files under `assets/fonts/` and are registered at runtime with
  `Pango.FontMap.add_font_file`. `register_fonts()` MUST run before anything
  resolves a font — Pango caches fontset lookups per description, so a single
  lookup made beforehand permanently pins that description to the fallback.
* **The stylesheet uses `$name` substitution, not `str.format`.** GTK CSS is
  full of literal braces (`@keyframes`, rule bodies); doubling every one of
  them for `.format()` makes a 200-line stylesheet unreadable. `$` never
  appears in CSS, so `string.Template` substitutes without escaping.
"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, PangoCairo  # noqa: E402

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FILES = (
    "Cinzel-Regular.ttf",
    "Cinzel-Bold.ttf",
    "Cinzel-Black.ttf",
    "AlegreyaSans-Regular.ttf",
    "AlegreyaSans-Medium.ttf",
    "AlegreyaSans-Bold.ttf",
    "AlegreyaSans-ExtraBold.ttf",
)

# Fallback chains: if font registration ever fails the layout still holds, it
# just renders in the nearest installed face instead of breaking.
DISPLAY = "'Cinzel','Noto Serif Display','Noto Serif',serif"
BODY = "'Alegreya Sans','Cantarell','Noto Sans',sans-serif"

# -- Dark Oak palette ---------------------------------------------------
PANEL_BG = "linear-gradient(158deg, rgba(42,30,18,.94), rgba(20,13,7,.96))"
PANEL_BORDER = "rgba(212,175,55,.5)"
RAIL_BG = "linear-gradient(160deg, rgba(24,16,9,.72), rgba(14,9,5,.78))"
INK = "#f1e4c8"
DIM = "#b39a6e"
GOLD = "#f4d47a"
RULE = "rgba(212,175,55,.28)"
WIN = "#7fca57"
TIE = "#e6b846"
LOSS = "#e37a5c"
SPELL = "#8fd0e6"

# Keyword pip colours, keyed by the letters Minion.flags emits. "G" (golden)
# is deliberately absent — it is the tile border treatment, not a pip.
PIP_COLOURS = {
    "T": "#9bb0c3",   # Taunt
    "D": "#ffd873",   # Divine Shield
    "P": "#74c957",   # Poison
    "V": "#46b98a",   # Venomous
    "W": "#86c9ef",   # Windfury
    "R": "#d78cf0",   # Reborn
    "S": "#c2cad6",   # Stealth
}

# Tavern-buff dot colours, keyed by the labels in state.game._BUFF_TAGS.
BUFF_COLOURS = {
    "Blood Gem": "#d0555f",
    "Elemental": "#3fb0c9",
    "Pirate": "#c9a13f",
    "Spell": "#a97fd0",
}

# The prototype stage every design token was drawn against. It is a full
# gameplay screenshot, so monitor-height / stage-height reproduces the mock's
# proportions on any display.
DESIGN_STAGE_W = 1180
DESIGN_STAGE_H = 707
MIN_SCALE, MAX_SCALE = 0.5, 3.0


def auto_scale(monitor_height: int) -> float:
    """Scale that makes the overlay occupy the same share of screen as the mock.

    1080p -> ~1.53, 1440p -> ~2.04, 4K -> clamped to 3.0.
    """
    if monitor_height <= 0:
        return 1.0
    return clamp_scale(round(monitor_height / DESIGN_STAGE_H, 2))


def clamp_scale(scale: float) -> float:
    return max(MIN_SCALE, min(MAX_SCALE, scale))


# Panel widths from the 1180x707 prototype stage, scaled to the monitor.
HUD_W = 300
BUFFS_W = 176
RAIL_W = 98
BOARD_MAX_W = 640
HOVER_MAX_W = 560
PANEL_PAD_X = 13
TILE_W = 80
PORTRAIT_D = 52
ORB_D = 40
HOVER_ORB_D = 38


_CSS = Template("""
/* Scoped to the overlay's own windows on purpose. This provider is attached to
   the whole *display*, so a bare `window` selector would make every other
   window this process opens — the settings window above all — transparent over
   whatever the system GTK theme happens to be. */
window.bg-overlay { background: transparent; }

/* ---- panel frame ------------------------------------------------- */
.hud {
    background-image: $panel_bg;
    border: ${b1}px solid $panel_border;
    border-radius: ${r12}px;
    padding: ${pad_y}px ${pad_x}px;
    color: $ink;
    font-family: $body;
    font-size: ${f13}px;
    box-shadow: 0 ${sh_y}px ${sh_b}px rgba(0,0,0,.55),
                inset 0 ${b1}px 0 rgba(255,255,255,.06);
}
.hud.rail {
    background-image: $rail_bg;
    border: ${b1}px solid rgba(212,175,55,.3);
    border-radius: ${r11}px;
    padding: ${p8}px ${p6}px;
}

/* ---- typography -------------------------------------------------- */
.title {
    font-family: $display;
    font-weight: 700;
    font-size: ${f12_5}px;
    letter-spacing: ${ls1_6}px;
    text-transform: uppercase;
    color: $gold;
    text-shadow: 0 ${b1}px ${b2}px rgba(0,0,0,.6);
}
.dim  { color: $dim; font-size: ${f11_5}px; }
.line { font-size: ${f13}px; color: $ink; }
.rule {
    background-color: $rule;
    min-height: ${b1}px;
    margin-top: ${p8}px;
}

/* ---- HUD: turn medallion ----------------------------------------- */
.medallion {
    min-width: ${med}px;
    min-height: ${med}px;
    border-radius: ${med}px;
    border: ${b2}px solid #d4af37;
    background-image: radial-gradient(circle at 50% 34%, #2a1d10, #120c06);
    box-shadow: inset 0 0 ${p9}px rgba(0,0,0,.65);
}
.medallion.combat { animation: turnpulse 2.4s ease-in-out infinite; }
@keyframes turnpulse {
    from { box-shadow: inset 0 0 ${p9}px rgba(0,0,0,.65),
                       0 0 ${p10}px rgba(244,212,122,.45); }
    to   { box-shadow: inset 0 0 ${p9}px rgba(0,0,0,.65),
                       0 0 ${p20}px rgba(244,212,122,.9); }
}
.medallion-num {
    font-family: $display;
    font-weight: 800;
    font-size: ${f23}px;
    color: $gold;
}
.medallion-cap {
    font-size: ${f7_5}px;
    font-weight: 700;
    letter-spacing: ${ls2}px;
    color: $dim;
}

/* ---- HUD: odds --------------------------------------------------- */
.odds-num {
    font-family: $display;
    font-weight: 800;
    font-size: ${f21}px;
}
.odds-num.win  { color: $win; }
.odds-num.tie  { color: $tie; }
.odds-num.loss { color: $loss; }
.odds-cap {
    font-size: ${f8}px;
    font-weight: 700;
    letter-spacing: ${ls1_5}px;
    color: $dim;
}
.bar-track {
    min-height: ${bar}px;
    border-radius: ${r5}px;
    border: ${b1}px solid rgba(212,175,55,.45);
    box-shadow: inset 0 ${b1}px ${b2}px rgba(0,0,0,.4);
}
.odds-hint {
    font-size: ${f11}px;
    font-weight: 700;
    letter-spacing: ${ls1_5}px;
    text-transform: uppercase;
    color: $dim;
}
.lethal {
    font-size: ${f11}px;
    font-weight: 700;
    letter-spacing: ${ls1_5}px;
    text-transform: uppercase;
    color: $loss;
}
.result {
    font-size: ${f11}px;
    font-weight: 700;
    letter-spacing: ${ls1_5}px;
    text-transform: uppercase;
    color: $dim;
}
.board-odds {
    font-size: ${f11}px;
    font-weight: 700;
    letter-spacing: ${ls1_5}px;
    color: $dim;
}
.result.win  { color: $win; }
.result.loss { color: $loss; }
.result.tie  { color: $tie; }
.bar-win  { background-image: linear-gradient(180deg,#8ed36a,#57a038); }
.bar-tie  { background-image: linear-gradient(180deg,#f0c85a,#cf9a1f); }
.bar-loss { background-image: linear-gradient(180deg,#dd6f52,#a83220); }

/* ---- HUD: damage pills ------------------------------------------- */
.pill {
    font-size: ${f12}px;
    font-weight: 600;
    padding: ${p3}px ${p9}px;
    border-radius: ${r20}px;
    color: $ink;
}
.pill.dealt {
    background-color: rgba(126,202,87,.14);
    border: ${b1}px solid rgba(126,202,87,.4);
}
.pill.taken {
    background-color: rgba(227,122,92,.14);
    border: ${b1}px solid rgba(227,122,92,.4);
}
.pill-arrow { font-size: ${f10}px; }
.pill-arrow.up   { color: $win; }
.pill-arrow.down { color: $loss; }
.pill-word { color: $dim; font-weight: 500; }

/* ---- minion tile ------------------------------------------------- */
.tile {
    min-width: ${tile}px;
    padding: ${p6}px ${p4}px ${p5}px;
    border-radius: ${r10}px;
    border: ${b1}px solid rgba(190,150,84,.4);
    background-image: linear-gradient(180deg, rgba(46,33,21,.96), rgba(24,16,9,.97));
    box-shadow: 0 ${p4}px ${p10}px rgba(0,0,0,.5),
                inset 0 ${b1}px 0 rgba(255,255,255,.06);
}
.tile.golden {
    border: ${b2}px solid transparent;
    border-image: linear-gradient(140deg,#fff2b0,#d4af37 45%,#8a6410) 1;
    box-shadow: 0 0 ${p14}px rgba(246,210,120,.75),
                inset 0 0 ${p12}px rgba(246,210,120,.35);
}
.portrait {
    border-radius: ${portrait}px;
    border: ${b2}px solid rgba(0,0,0,.5);
    background-image: radial-gradient(circle at 38% 30%,
        rgba(255,255,255,.28), rgba(58,42,26,.9) 55%, rgba(0,0,0,.6));
    box-shadow: inset 0 ${b2}px ${p6}px rgba(255,255,255,.22),
                inset 0 -${p4}px ${p8}px rgba(0,0,0,.55);
}
.gem {
    min-width: ${gem}px;
    min-height: ${gem}px;
    border-radius: ${gem}px;
    border: ${b1}px solid rgba(255,255,255,.35);
    font-family: $display;
    font-weight: 800;
    font-size: ${f14}px;
    color: #ffffff;
    text-shadow: 0 ${b1}px ${b2}px rgba(0,0,0,.85);
    box-shadow: 0 ${b2}px ${p4}px rgba(0,0,0,.5);
}
.gem.atk { background-image: radial-gradient(circle at 40% 32%, #ffe6a6, #e0a53a 55%, #a9701c); }
.gem.hp  { background-image: radial-gradient(circle at 40% 32%, #ff9e84, #d5462c 55%, #8f2317); }
.tile-name {
    font-size: ${f10}px;
    font-weight: 600;
    letter-spacing: ${ls0_2}px;
    color: #f1e2c4;
    text-shadow: 0 ${b1}px ${b2}px rgba(0,0,0,.8);
}
.pip {
    min-width: ${pip}px;
    min-height: ${pip}px;
    border-radius: ${r3}px;
    font-size: ${f9}px;
    font-weight: 800;
    color: #1a1206;
    box-shadow: 0 ${b1}px ${b1}px rgba(0,0,0,.5);
}
$pip_rules

/* ---- leaderboard rail -------------------------------------------- */
.rail-row {
    padding: ${p4}px ${p7}px;
    border-radius: ${r9}px;
    border-left: ${p3}px solid transparent;
}
.rail-row.hot {
    background-color: rgba(244,212,122,.18);
    border-left: ${p3}px solid $gold;
}
.orb {
    border-radius: ${orb}px;
    border: ${b2}px solid rgba(0,0,0,.5);
    background-image: radial-gradient(circle at 38% 30%,
        rgba(255,255,255,.28), rgba(58,42,26,.9) 55%, rgba(0,0,0,.6));
    box-shadow: inset 0 -${p3}px ${p6}px rgba(0,0,0,.45);
}
.orb.you {
    border: ${b2}px solid $gold;
    box-shadow: 0 0 ${p10}px rgba(244,212,122,.7);
}
.orb.dead { filter: grayscale(1); opacity: 0.5; }
.rank {
    min-width: ${rank}px;
    min-height: ${rank}px;
    padding: 0 ${p4}px;
    border-radius: ${r9}px;
    font-family: $display;
    font-weight: 800;
    font-size: ${f10_5}px;
    background-color: rgba(12,8,4,.92);
    color: #f2e2b6;
    border: ${b1}px solid rgba(0,0,0,.55);
    box-shadow: 0 ${b1}px ${p3}px rgba(0,0,0,.6);
}
.rank.you {
    background-image: linear-gradient(180deg,#f6e07f,#c9992f);
    color: #2a1c07;
}
.rail-hp {
    font-size: ${f12}px;
    font-weight: 700;
    color: #c9b892;
    text-shadow: 0 ${b1}px ${b2}px rgba(0,0,0,.85);
}
.rail-hp.you  { color: $gold; }
.rail-hp.dead { color: #c07a7a; text-decoration-line: line-through; }

/* ---- tavern buffs ------------------------------------------------ */
.buff-dot {
    min-width: ${dot}px;
    min-height: ${dot}px;
    border-radius: ${dot}px;
}
$buff_rules
.buff-label { color: $ink; font-weight: 600; font-size: ${f12_5}px; }
.buff-val {
    font-family: $display;
    font-weight: 700;
    font-size: ${f12_5}px;
    color: $gold;
}
.spell { color: $spell; font-weight: 600; font-size: ${f12_5}px; }

/* ---- scout popout notch ------------------------------------------ */
.notch { color: rgba(30,20,11,.95); font-size: ${f16}px; }

/* ---- edit mode (behaviour unchanged, gilded) ---------------------- */
.grip {
    color: $gold;
    font-family: $body;
    font-size: ${f11}px;
    font-weight: 700;
    letter-spacing: ${ls1_5}px;
    text-transform: uppercase;
}
.editing { border: ${b2}px dashed rgba(244,212,122,.9); }
/* The gear is the one clickable pixel-patch on a locked overlay, so it stays
   quiet until the pointer finds it. */
.gearbtn {
    background-image: radial-gradient(circle at 50% 38%, rgba(58,42,26,.92), rgba(18,12,6,.94));
    border: ${b1}px solid rgba(212,175,55,.45);
    border-radius: ${gear}px;
    min-width: ${gear}px;
    min-height: ${gear}px;
    padding: 0;
    color: rgba(244,212,122,.55);
    font-size: ${f14}px;
    opacity: 0.45;
}
.gearbtn:hover {
    opacity: 1;
    color: $gold;
    border: ${b1}px solid $gold;
    box-shadow: 0 0 ${p10}px rgba(244,212,122,.55);
}
.lockbtn {
    background-image: linear-gradient(180deg,#f6e07f,#d4af37 55%,#a97e1f);
    color: #3a2708;
    border: ${b1}px solid #7a5a1e;
    border-radius: ${r9}px;
    padding: ${p8}px ${p18}px;
    font-family: $display;
    font-weight: 800;
    font-size: ${f13}px;
    letter-spacing: ${ls0_5}px;
    box-shadow: 0 ${p6}px ${p16}px rgba(0,0,0,.5),
                inset 0 ${b1}px 0 rgba(255,255,255,.5);
}
""")


# The settings window is chrome, not overlay: it is deliberately NOT scaled by
# `overlay_scale`, which exists to match the game HUD's share of the screen and
# would render a perfectly ordinary preferences window at 3x on a 4K display.
#
# **Colours only on anything GTK owns.** Widget internals — switch sliders,
# scrollbar sliders, spin steppers, the titlebar's window controls — get their
# minimum sizes, padding and baselines from the theme's own metrics, and
# overriding any of those from here makes GTK compute negative minimums and
# warn on every single layout pass. So: background and foreground on GTK parts,
# and geometry only on the classes we create ourselves. `prefer_dark()` is what
# makes the untouched parts look right.
_SETTINGS_CSS = Template("""
window.bg-settings {
    background-image: linear-gradient(158deg, #2a1e12, #140d07);
    color: $ink;
    font-family: $body;
    font-size: 14px;
}
window.bg-settings headerbar {
    background-image: linear-gradient(180deg, rgba(58,42,26,.98), rgba(30,20,11,.98));
    border-bottom: 1px solid $panel_border;
    box-shadow: none;
    color: $gold;
}
window.bg-settings headerbar label {
    font-family: $display;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: $gold;
}
window.bg-settings stacksidebar {
    background-color: rgba(12,8,4,.55);
    border-right: 1px solid $rule;
}
window.bg-settings stacksidebar list,
window.bg-settings scrolledwindow,
window.bg-settings viewport,
window.bg-settings box,
window.bg-settings list {
    background-color: transparent;
}
window.bg-settings stacksidebar row {
    padding: 9px 14px;
    border-radius: 8px;
    color: $dim;
    font-weight: 600;
}
window.bg-settings stacksidebar row:selected {
    background-color: rgba(244,212,122,.16);
    color: $gold;
}

.settings-section {
    font-family: $display;
    font-weight: 700;
    font-size: 15px;
    letter-spacing: 1.6px;
    text-transform: uppercase;
    color: $gold;
}
.settings-label { color: $ink; font-weight: 600; font-size: 14px; }
.settings-help  { color: $dim; font-size: 12px; }
.settings-row   { border-bottom: 1px solid rgba(212,175,55,.16); padding: 9px 2px; }
.settings-note  { color: $dim; font-size: 12px; }
.settings-mono {
    font-family: monospace;
    font-size: 12px;
    color: $ink;
}
.settings-ok   { color: $win; font-weight: 700; }
.settings-bad  { color: $loss; font-weight: 700; }

/* Our own buttons carry a class, so nothing here can reach the titlebar's
   window controls — whose padding and baselines belong to the theme. */
.settings-btn {
    background-image: linear-gradient(180deg, rgba(70,52,32,.95), rgba(38,26,14,.95));
    border: 1px solid rgba(212,175,55,.45);
    color: $ink;
    font-weight: 600;
}
.settings-btn:hover {
    background-image: linear-gradient(180deg, rgba(92,69,42,.95), rgba(52,36,20,.95));
    border: 1px solid $gold;
}
.settings-btn:disabled { color: $dim; }
.settings-btn.gold {
    background-image: linear-gradient(180deg,#f6e07f,#d4af37 55%,#a97e1f);
    border: 1px solid #7a5a1e;
    color: #3a2708;
    font-weight: 800;
}
.settings-btn.danger {
    background-image: linear-gradient(180deg, rgba(96,40,28,.95), rgba(58,22,15,.95));
    border: 1px solid rgba(227,122,92,.6);
    color: #ffd9cd;
}

/* Colour only from here down: sizing on these parts belongs to the theme. */
window.bg-settings entry,
window.bg-settings spinbutton,
window.bg-settings spinbutton text {
    background-image: none;
    background-color: rgba(10,7,3,.75);
    color: $ink;
}
window.bg-settings switch:checked {
    background-image: linear-gradient(180deg,#f6e07f,#c9992f);
}
window.bg-settings popover > contents {
    background-color: #1d140b;
    color: $ink;
}
window.bg-settings separator { background-color: $rule; }
""")


def settings_stylesheet() -> str:
    """Dark Oak for the settings window, at a fixed size (see `_SETTINGS_CSS`)."""
    return _SETTINGS_CSS.substitute(
        ink=INK, dim=DIM, gold=GOLD, rule=RULE, panel_border=PANEL_BORDER,
        win=WIN, loss=LOSS, display=DISPLAY, body=BODY,
    )


def px(value: float, scale: float) -> int:
    """Scale a design-token pixel size, never rounding a visible edge to 0."""
    return max(1, round(value * scale))


def _dims(scale: float) -> dict[str, int]:
    sizes = {
        # borders / hairlines
        "b1": 1, "b2": 2,
        # radii
        "r3": 3, "r5": 5, "r9": 9, "r10": 10, "r11": 11, "r12": 12, "r20": 20,
        # generic paddings
        "p3": 3, "p4": 4, "p5": 5, "p6": 6, "p7": 7, "p8": 8, "p9": 9,
        "p10": 10, "p12": 12, "p14": 14, "p16": 16, "p18": 18, "p20": 20,
        # font sizes
        "f7_5": 7.5, "f8": 8, "f9": 9, "f10": 10, "f10_5": 10.5, "f11": 11,
        "f11_5": 11.5, "f12": 12, "f12_5": 12.5, "f13": 13, "f14": 14,
        "f16": 16, "f21": 21, "f23": 23,
        # letter spacing
        "ls0_2": 0.2, "ls0_5": 0.5, "ls1_5": 1.5, "ls1_6": 1.6, "ls2": 2,
        # component sizes
        "pad_x": 13, "pad_y": 11, "sh_y": 12, "sh_b": 34,
        "med": 58, "bar": 9, "tile": TILE_W, "portrait": PORTRAIT_D,
        "gem": 25, "pip": 13, "orb": ORB_D, "rank": 17, "dot": 8,
        # Deliberately small: it is a hole in the overlay's click-through.
        "gear": 22,
    }
    return {k: px(v, scale) for k, v in sizes.items()}


def stylesheet(scale: float = 1.0) -> str:
    """The full Dark Oak stylesheet, with every size scaled by `scale`."""
    dims = _dims(scale)
    pip_rules = "\n".join(
        f".pip-{letter.lower()} {{ background-color: {colour}; }}"
        for letter, colour in PIP_COLOURS.items()
    )
    buff_rules = "\n".join(
        f".buff-dot.buff-{label.lower().replace(' ', '')} "
        f"{{ background-color: {colour}; box-shadow: 0 0 {dims['p5']}px {colour}; }}"
        for label, colour in BUFF_COLOURS.items()
    )
    return _CSS.substitute(
        panel_bg=PANEL_BG, panel_border=PANEL_BORDER, rail_bg=RAIL_BG,
        ink=INK, dim=DIM, gold=GOLD, rule=RULE,
        win=WIN, tie=TIE, loss=LOSS, spell=SPELL,
        display=DISPLAY, body=BODY,
        pip_rules=pip_rules, buff_rules=buff_rules,
        **dims,
    )


_fonts_registered = False


def register_fonts() -> bool:
    """Register the bundled Cinzel/Alegreya Sans faces with the default fontmap.

    Idempotent, and safe to call before Gtk.init — but it must run before ANY
    font lookup, since Pango caches the fallback it picks for a description.
    Returns False (and logs) if a face is missing, in which case the CSS
    fallback chains take over.
    """
    global _fonts_registered
    if _fonts_registered:
        return True
    fontmap = PangoCairo.FontMap.get_default()
    if not hasattr(fontmap, "add_font_file"):
        log.warning("Pango < 1.56: bundled fonts unavailable, using fallbacks")
        return False
    ok = True
    for name in FONT_FILES:
        path = FONT_DIR / name
        try:
            if not fontmap.add_font_file(str(path)):
                raise RuntimeError("add_font_file returned False")
        except Exception as exc:
            log.warning("could not register bundled font %s (%s)", name, exc)
            ok = False
    _fonts_registered = ok
    return ok


def prefer_dark() -> None:
    """Ask GTK for the dark variant of whatever theme is installed.

    Our own CSS covers our own widgets, but not GTK's: dropdown popovers,
    scrollbars, tooltips, text-selection and focus colours all come from the
    system theme, and on a light one they render as bright rectangles in the
    middle of a Dark Oak window. This is the non-libadwaita way to say "dark".
    """
    gtk_settings = Gtk.Settings.get_default()
    if gtk_settings is not None:  # None before Gtk.init, e.g. in tests
        gtk_settings.set_property("gtk-application-prefer-dark-theme", True)


def _add(display, css: str) -> Gtk.CssProvider:
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    return provider


def install(display, scale: float = 1.0) -> Gtk.CssProvider:
    """Register fonts, then apply the overlay stylesheet to `display`.

    Returns the provider so a scale change can take it back off again — these
    attach to the display, not the window, so a rebuild that only added a new
    one would leave both live and let whichever loaded first keep winning.
    """
    register_fonts()
    prefer_dark()
    return _add(display, stylesheet(scale))


def install_settings(display) -> Gtk.CssProvider:
    """Apply the (unscaled) settings-window stylesheet to `display`."""
    register_fonts()
    prefer_dark()
    return _add(display, settings_stylesheet())


def uninstall(provider: Gtk.CssProvider | None, display=None) -> None:
    if provider is None:
        return
    from gi.repository import Gdk

    display = display or Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.remove_provider_for_display(display, provider)
