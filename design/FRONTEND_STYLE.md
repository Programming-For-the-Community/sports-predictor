# Frontend Visual Style — "Arena"

The house visual language for the Flutter Web app (`/frontend`). It is a dark, data-dense
"sportsbook terminal" aesthetic: calm navy surfaces, a single electric-cyan accent, a
violet secondary reserved for field-event sports, and large confident numerals. The goal
is a board you can read at a glance — probabilities and margins are the loudest things on
screen, chrome is quiet.

Apply this for any new screen. When a choice isn't covered here, derive it from the tokens
below rather than introducing a new color or font.

## Foundations

### Fonts

Two families, loaded via the `google_fonts` Flutter package. Never add a third.

| Role | Family | Weights | Used for |
|---|---|---|---|
| Display / UI | `Space Grotesk` | 400 / 500 / 600 / 700 | Headings, team names, big numerals, body copy |
| Data / labels | `IBM Plex Mono` | 400 / 500 / 600 | Micro-labels, metrics, timestamps, IDs, odds |

Rule of thumb: anything that is a **measured value or a label** is mono; anything that is
**prose or a name** is Space Grotesk. Micro-labels are mono, uppercase, with wide tracking
(`letter-spacing: 0.1em–0.22em`) and colored `#586577`.

### Color tokens

Define these as `const Color` values in a `AppColors` class (or via `ThemeData`). Never hardcode hex values inline — always reference these names.

```dart
// Surfaces
bg           = Color(0xFF0a0e17)  // app background (Scaffold backgroundColor)
bgDeep       = Color(0xFF070a12)  // behind the app
surface      = Color(0x09FFFFFF)  // flat card (rgba 255,255,255,0.035)
surfaceGrad  = [Color(0x0DFFFFFF), Color(0x05FFFFFF)]  // raised card gradient stops
inset        = Color(0x08FFFFFF)  // nested panel inside a card
border       = Color(0x12FFFFFF)  // default hairline (0x14 on raised cards)

// Text
ink          = Color(0xFFEAF0F7)  // primary
inkMid       = Color(0xFFCDD5DE)  // secondary values
inkSub       = Color(0xFF8A96A8)  // supporting copy, de-emphasized team
inkMute      = Color(0xFF586577)  // micro-labels, captions

// Accents
cyan         = Color(0xFF22D3EE)  // PRIMARY — win side, picks, active emphasis, links
cyan2        = Color(0xFF5EEAD4)  // gradient partner for cyan
violet       = Color(0xFF7C6CFF)  // SECONDARY — field-event sports (PGA, F1), tertiary cards
violet2      = Color(0xFFA99DFF)  // violet text on dark

// Signal
pos          = Color(0xFF22D3EE)  // positive feature contribution (uses cyan)
neg          = Color(0xFFFF5C7A)  // negative contribution / loss
neg2         = Color(0xFFFF8FA3)  // gradient partner for neg
live         = Color(0xFF4ADE80)  // "live / active" status dot + pill
warn         = Color(0xFFFFB454)  // warming / preseason / off-cadence; possession football; pending app update
```

Accent discipline: **cyan is the only call-to-attention color.** Violet is structural, not
decorative — use it only to tag field-event sports and the occasional tertiary card (e.g.
"Recent form"). Green (`--live`) and amber (`--warn`) are status semantics only, never brand
accents. Amber also marks two live-state signals: the possession football (which team has the
ball) and the pending-update banner/dot in the Android app.

### Signature gradients

Expressed as Flutter `LinearGradient` / `RadialGradient` values:

```dart
brandMark   = LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight,
                colors: [Color(0xFF22D3EE), Color(0xFF7C6CFF)])  // logo tile, avatar
cyanFill    = LinearGradient(colors: [Color(0xFF22D3EE), Color(0xFF5EEAD4)])  // prob bars, positive
violetFill  = LinearGradient(colors: [Color(0xFF7C6CFF), Color(0xFFA99DFF)])  // field-event emphasis
negFill     = LinearGradient(colors: [Color(0xFFFF5C7A), Color(0xFFFF8FA3)]) // negative bars
accentStripH2h   = LinearGradient(colors: [Color(0xFF22D3EE), Color(0xFF5EEAD4)]) // 4px card top (h2h)
accentStripField = LinearGradient(colors: [Color(0xFF7C6CFF), Color(0xFF22D3EE)]) // 4px card top (field)
glow        = RadialGradient(colors: [Color(0x1A22D3EE), Color(0x0D7C6CFF), Colors.transparent],
                stops: [0, 0.45, 0.70])
```

The `glow` is a single non-interactive `Container` with the radial gradient, positioned at the top-center of each page behind all content (`Stack` with it at index 0, ~1100×560 logical px). One glow per page.

Big "favored side" percentages use a **gradient-clipped** number for extra pop via Flutter's `ShaderMask` widget wrapping a `Text`, using `cyanFill` as the shader and `BlendMode.srcIn`.

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

- **Top bar** — sticky `AppBar` or `SliverAppBar`, background `Color(0xD20a0e17)` with `ImageFilter.blur(sigmaX:12, sigmaY:12)` via `BackdropFilter`, bottom hairline `Divider`. Left: gradient `brandMark` tile (34px, `BorderRadius.circular(10)`) + product name. Center/left: a pill-group segmented toggle (active segment = solid cyan with `bg` text, inactive = muted). Right cluster: a `live` status pill, a mono timestamp, and a pill user chip with a `brandMark` avatar.
- **Stat pill** — `--surface` card, radius 16px, mono micro-label over a 32px 700 numeral
  (cyan when it's the highlighted metric).
- **Sport card** — raised `surface-grad` card with a 4px top accent strip (cyan h2h / violet
  field). Header: glowing accent dot + sport name, mono shape label beneath, status pill
  top-right. A metric trio (Model / Hit rate / Brier — hit rate in cyan). Footer is a nested
  `--inset` panel: slate label + CTA on one row, top pick + value on the next. Hover lifts
  `translateY(-2px)` and brightens the border to cyan. Inactive (model not live) cards are
  non-interactive with a muted "VIEW-ONLY" CTA.
- **Game row (list)** — full-width rounded card, `display:flex; flex-wrap:wrap`, with a 4px
  **left accent bar in the favored team's color**. Slot (mono) · a single team line (color dot +
  abbr + score per side, "away `@` home") · a win-probability split bar · margin (18px 700) · a
  confidence pill. Hover brightens + lifts 1px. Status in the bar's slot: upcoming games show
  only the win-probability bar (no status text), live games the LIVE pill + clock, finished
  games just FINAL (never ESPN's own "Final" text beside it).
- **Matchup hero (detail)** — gradient-tinted panel, two columns of team + record + big
  percentage (favored side gradient-clipped cyan), an `@` pill between, a plain 1px `border`
  divider under the venue (each side's percentage is already printed under its team), then a
  Pick / Pred margin / Pred total stat trio.
- **Live football situation** (NFL/NCAAFB, live only) — an amber `PossessionBall` (14px oval,
  white laces) marks the team with the ball: at the end of its line in the game row (a fixed
  14px slot on both lines so scores never shift) and before the team name in the matchup hero
  (only there — never on the down line). The down line is mono micro-label text: down and
  distance in ink, then field position, then `Red zone` in `--neg` inside the 20 — joined with
  ` · ` in lists, `1st & Goal at JAX 6` in the hero. On a phone the down line sits under the
  clock; on a wide row, beside it. Both disappear whenever ESPN reports no situation (breaks,
  reviews).
- **Stacked leaders (phone, football)** — each player is a white live row over a cyan
  predicted row; a 1px `border` hairline separates players (none above the first). No row
  labels or color key.
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

Intrinsic, **no hardcoded breakpoints**: use `LayoutBuilder` or `MediaQuery` for type and padding scaling, `Wrap` with `spacing`/`runSpacing` for card grids (minimum child width ~340 logical px), and `Wrap` + `Flexible` on rows. When a grid's cards should fill the row, size them with `fillCardWidth` (`core/widgets/responsive.dart`) so the row stretches evenly instead of leaving a strip of empty space at the right edge. The page scrolls via a top-level `SingleChildScrollView` or `CustomScrollView`; never trap scroll inside a fixed-height inner widget. The one exception, the season bracket's pinned-scrollbar pane, applies on desktop only — on touch screens (Android/iOS, app or mobile browser) the bracket renders inline, since a nested pane there traps every swipe.

## Android app

The Android app is the same Flutter UI with a few additions that only exist there (`AppShell.androidApp`):

- **Header** — a settings gear (with an amber dot while an update is pending) replaces Sign out; Sign out lives in Settings › Notifications, beside when the 30-day sign-in expires.
- **Update banner** — an amber-tinted strip on Home, "vX is ready", with Later / Update; Later hides it for a day.
- **Settings pages** — Notifications (per-sport weekly-report switches), App updates (installed vs. latest, notes, Download & install with progress), built from `MobilePanel` surfaces.
- **Home-screen widgets** — native Android layouts (`android/app/src/main/res/layout/widget_*.xml`) in the same palette: `bg` at ~92% opacity, 20dp radius, ink text, cyan values, cyan-over-muted-red pick bars. Each has a sport picker on placement.
- **Website, Android browsers only** — a small outlined cyan "Get app" pill in the header next to Sign out.

User-facing release notes (update notification, App updates page) come from `Source/front-end/RELEASE_NOTES.txt`: one or two short sentences about features and fixes, never technical detail — they're read on a phone screen.

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
