# Frontend Visual Style — "Arena"

The house visual language for the React SPA (`/frontend`). It is a dark, data-dense
"sportsbook terminal" aesthetic: calm navy surfaces, a single electric-cyan accent, a
violet secondary reserved for field-event sports, and large confident numerals. The goal
is a board you can read at a glance — probabilities and margins are the loudest things on
screen, chrome is quiet.

Apply this for any new screen. When a choice isn't covered here, derive it from the tokens
below rather than introducing a new color or font.

## Foundations

### Fonts

Two families, loaded from Google Fonts. Never add a third.

| Role | Family | Weights | Used for |
|---|---|---|---|
| Display / UI | `Space Grotesk` | 400 / 500 / 600 / 700 | Headings, team names, big numerals, body copy |
| Data / labels | `IBM Plex Mono` | 400 / 500 / 600 | Micro-labels, metrics, timestamps, IDs, odds |

Rule of thumb: anything that is a **measured value or a label** is mono; anything that is
**prose or a name** is Space Grotesk. Micro-labels are mono, uppercase, with wide tracking
(`letter-spacing: 0.1em–0.22em`) and colored `#586577`.

### Color tokens

```
/* Surfaces */
--bg            #0a0e17   /* app background */
--bg-deep       #070a12   /* html/body behind the app */
--surface       rgba(255,255,255,0.035)                                   /* flat card */
--surface-grad  linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02)) /* raised card */
--inset         rgba(255,255,255,0.03)   /* nested panel inside a card */
--border        rgba(255,255,255,0.07)   /* default hairline (0.08 on raised cards) */

/* Text */
--ink           #eaf0f7   /* primary */
--ink-mid       #cdd5de   /* secondary values */
--ink-sub       #8a96a8   /* supporting copy, de-emphasized team */
--ink-mute      #586577   /* micro-labels, captions */

/* Accents */
--cyan          #22d3ee   /* PRIMARY — win side, picks, active emphasis, links */
--cyan-2        #5eead4   /* gradient partner for cyan */
--violet        #7c6cff   /* SECONDARY — field-event sports (PGA, F1), tertiary cards */
--violet-2      #a99dff   /* violet text on dark */

/* Signal */
--pos           #22d3ee   /* positive feature contribution (uses cyan) */
--neg           #ff5c7a   /* negative contribution / loss */
--neg-2         #ff8fa3   /* gradient partner for neg */
--live          #4ade80   /* "live / active" status dot + pill */
--warn          #ffb454   /* warming / preseason / off-cadence */
```

Accent discipline: **cyan is the only call-to-attention color.** Violet is structural, not
decorative — use it only to tag field-event sports and the occasional tertiary card (e.g.
"Recent form"). Green (`--live`) and amber (`--warn`) are status semantics only, never brand
accents.

### Signature gradients

```
brand-mark   linear-gradient(135deg, #22d3ee, #7c6cff)   /* logo tile, avatar */
cyan-fill    linear-gradient(90deg,  #22d3ee, #5eead4)    /* progress / prob bars, positive */
neg-fill     linear-gradient(90deg,  #ff5c7a, #ff8fa3)    /* negative contribution bars */
accent-strip linear-gradient(90deg,  #22d3ee, #5eead4)    /* 4px card top strip (h2h) */
accent-strip linear-gradient(90deg,  #7c6cff, #22d3ee)    /* 4px card top strip (field event) */
glow         radial-gradient(ellipse at center, rgba(34,211,238,0.10), rgba(124,108,255,0.05) 45%, transparent 70%)
```

The `glow` is a single fixed, non-interactive layer behind the page content
(`position:fixed; top:-220px; left:50%; translateX(-50%); ~1100×560px; z-index:0`). Page
content sits at `z-index:1`. One glow per page, top-center.

Big "favored side" percentages use a **gradient-clipped** number for extra pop:
`background:var(--cyan-fill); -webkit-background-clip:text; background-clip:text;
-webkit-text-fill-color:transparent;`

## Shape, depth & spacing

| Token | Value | Notes |
|---|---|---|
| Radius — card | `16–20px` | 18px sport card, 20px detail panels |
| Radius — pill | `999px` | toggles, status chips, conf badges, avatars |
| Radius — nested | `12px` | inset panels inside a card |
| Shadow — card | `0 10px 30px rgba(0,0,0,0.25)` | |
| Shadow — hero | `0 12px 36px rgba(0,0,0,0.30)` | matchup header |
| Card top strip | `4px` full-bleed gradient | sport accent; h2h = cyan, field = violet |
| Card padding | `26–34px` | |
| Section gap | `18–22px` | |
| Page padding | `clamp(28px,4vw,56px) clamp(20px,4vw,48px)` | |
| Page max-width | `1300–1520px`, centered | board 1520, list/detail 1300 |

Accent dots glow: a status/team dot gets `box-shadow: 0 0 10px <its color>`.

## Type scale

| Element | Size | Weight | Notes |
|---|---|---|---|
| Page H1 | `clamp(32px, 4.5vw, 52px)` | 700 | tight tracking `-0.025em`, `white-space:nowrap` on short titles |
| Big stat numeral | `32–52px` | 700 | win prob, KPIs; gradient-clip the favored side |
| Card title (team / sport) | `26px` | 700 | `white-space:nowrap` |
| Section title | `16px` | 600 | inside cards |
| Body / metric value | `14–19px` | 500–700 | |
| Micro-label | `10–12px` | 400–600 | MONO, uppercase, `letter-spacing 0.1–0.22em`, `#586577` |

Separator between meta items is a middle dot with spaces: ` · `. Headings are sentence case;
labels are UPPERCASE mono.

## Components

- **Top bar** — sticky, `rgba(10,14,23,0.82)` + `backdrop-filter: blur(12px)`, bottom hairline.
  Left: gradient `brand-mark` tile (34px, radius 10px) + product name. Center/left: a
  pill-group segmented toggle (active segment = solid cyan with `#0a0e17` text, inactive =
  muted). Right cluster: a `--live` status pill, a mono timestamp, and a pill user chip with a
  `brand-mark` avatar.
- **Stat pill** — `--surface` card, radius 16px, mono micro-label over a 32px 700 numeral
  (cyan when it's the highlighted metric).
- **Sport card** — raised `surface-grad` card with a 4px top accent strip (cyan h2h / violet
  field). Header: glowing accent dot + sport name, mono shape label beneath, status pill
  top-right. A metric trio (Model / Hit rate / Brier — hit rate in cyan). Footer is a nested
  `--inset` panel: slate label + CTA on one row, top pick + value on the next. Hover lifts
  `translateY(-2px)` and brightens the border to cyan. Inactive (model not live) cards are
  non-interactive with a muted "VIEW-ONLY" CTA.
- **Game row (list)** — full-width rounded card, `display:flex; flex-wrap:wrap`, with a 4px
  **left accent bar in the favored team's color**. Slot (mono) · two stacked team lines (color
  dot + abbr + mono record; favored team is `--ink`/600, underdog `--ink-sub`/400) · a
  win-probability split bar · margin (18px 700) · a confidence pill. Hover brightens + lifts 1px.
- **Matchup hero (detail)** — gradient-tinted panel, two columns of team + record + big
  percentage (favored side gradient-clipped cyan), an `@` pill between, a 12px split bar, then a
  Pick / Pred margin / Pred total stat trio.
- **Feature attribution** — center-diverging horizontal bars from a faint 1px centerline:
  positive contributions extend right with `cyan-fill`, negative left with `neg-fill`, all
  rounded (`border-radius:999px`) and normalized so the largest magnitude ≈ half-width. Signed
  mono value on the right (cyan / `--neg`).
- **Model card / prediction history** — quiet `--surface` cards; history is a stack of thin
  rounded track bars, current version filled with `cyan-fill`, prior versions a flat slate.
- **Recent-form card** — the one place violet leads: violet-tinted surface + border, violet title.

## Data-viz conventions

- **Win-probability split bar**: favored segment = `cyan-fill`, underdog = translucent slate
  `#33405580`, track `#1a2233`, rounded ends. Widths are the live percentages — the only place
  a width should be a runtime value.
- **Confidence tiers** from distance off 50/50: `edge ≥ 0.13 → HIGH` (cyan), `≥ 0.06 → MED`
  (amber), else `LOW` (muted).
- **Probabilities** display as `NN%` by default; support an American-odds format
  (`p≥.5 → -round(100p/(1-p))`, else `+round(100(1-p)/p)`) as a user toggle.
- **Head-to-head vs field event**: h2h shows win prob + margin; field events show a
  finishing-position distribution. Never force a field event into the win/loss shape — tag it
  violet and render a distribution instead.

## Responsiveness

Intrinsic, **no media queries**: `clamp()` for type and padding, `grid-template-columns:
repeat(auto-fill, minmax(340px, 1fr))` for card grids, `flex-wrap` + `min-width` on rows. The
body scrolls; never trap scroll in an inner wrapper.

## Do / Don't

- **Do** keep one accent (cyan) loud and everything else quiet. **Don't** add a second
  attention color or use violet decoratively.
- **Do** make probabilities and margins the largest type on the screen. **Don't** let chrome or
  labels compete with the numbers.
- **Do** use mono only for values and labels. **Don't** set prose or names in mono.
- **Do** lean on radius, soft shadow, and the top accent strip for hierarchy. **Don't** use
  heavy borders or saturated card fills.
- **Do** reuse the gradient tokens verbatim. **Don't** invent new gradients or hex values.
- **Do** keep emoji and decorative SVG out of the UI; use real team colors as the only
  per-item color.
