# Data Sources

Free-first, per sport. Where a genuinely free source has a quality or completeness gap, an optional paid upgrade is noted — but every sport has a free path that's enough to build and run the pipeline.

| Sport | Primary source (free) | Auth | Update cadence | Notes |
|---|---|---|---|---|
| NFL | nflverse / nflfastR | None | Weekly in-season (re-published files) | GitHub-hosted parquet/CSV files: play-by-play, schedules, rosters back to 1999. The most mature open sports data ecosystem of the six. |
| NCAA FB | CollegeFootballData.com (CFBD) | Free API key (registration required) | Near-daily | REST API covering play-by-play, advanced team stats, betting lines, and recruiting. A Patreon-funded tier exists for higher rate limits, but the free tier is sufficient for a personal pipeline. |
| NBA | nba_api (wraps stats.nba.com) or balldontlie.io | None (nba_api) / free-tier key (balldontlie) | Daily | nba_api is unofficial and occasionally breaks when the NBA changes its endpoints — balldontlie is a simpler, more stable hosted REST API if you'd rather not deal with that. |
| NCAA MBB | ESPN's public (unofficial) endpoints, or the hoopR package's underlying sources | None | Daily | Covers schedules, box scores, and play-by-play. KenPom's advanced efficiency ratings are a paid optional add-on if you want them as a feature later. |
| PGA Tour | ESPN's public (unofficial) golf endpoints; Sports-Reference for historical backfill | None | Weekly (tournament-based, ~45 events/year) | Data Golf is a worthwhile optional paid upgrade — it publishes its own win/top-5/top-10/top-20/make-cut probability model alongside player skill ratings, which you could use directly as a baseline or as a feature input rather than building golf-specific modeling from scratch. |
| F1 | Jolpica-F1 (Ergast-compatible) | None, rate-limited to 200 requests/hour unauthenticated | Weekly (race-based, ~24 events/year) | Drop-in successor to the now-shut-down Ergast API, using the same endpoint structure. FastF1 (Python) wraps Jolpica and adds access to lap timing and telemetry summaries if you want richer features later — recommend storing only derived summaries (sector deltas, pit stop timing) rather than raw telemetry, which is large and unnecessary for outcome prediction. |

## Ingestion notes

**Polling frequency should match the sport's actual cadence**, not a single global schedule — daily is plenty for NFL/NCAA FB/PGA/F1, while NBA and NCAA MBB benefit from more frequent pulls given how many games happen per day in-season. The sport registry (`DATA_SCHEMA.md`) is where this cadence lives once Phase 4's orchestration is in place.

**Treat unofficial/hidden APIs (ESPN, stats.nba.com) as best-effort.** They're free and reliable in practice, but undocumented endpoints can change without notice. Build the corresponding adapter's `fetch()` step to fail loudly (log and alert) rather than silently returning empty data, so a broken endpoint shows up as a CloudWatch alarm instead of a quietly stale model.

**Historical backfill is rate-limit-bound, not compute-bound.** Pulling 10 years of data respecting each source's request limits (Jolpica's 200/hour, in particular) will take real wall-clock time — plan for the backfill to run over a day or two in the background rather than expecting it to complete in one sitting.

**Deliberately excluded for now:** SportsRadar, Genius Sports, SportsDataIO, and similar commercial feeds. These offer broader coverage and guaranteed uptime SLAs, but at a monthly cost that doesn't make sense for a personal project when the free sources above already cover everything the model needs. Worth revisiting only if a specific free source becomes unreliable enough to be a real problem.
