# PGA Feature Engineering

Describes what PGA's model-training features are, how they're computed, and where each field comes from. Covers the same ground `design/DATA_SCHEMA.md` covers for storage -- this doc is specifically about the derived training data, not the raw DynamoDB/S3 schema it's built from.

**Status: fully built and live.** Feature engineering, training (now 7 models across 4 datasets -- the original 5 stroke-play/cutline/round targets below, plus cup and individual-match win-probability, see "Cup and match win-probability" below), inference (`predict`/`predict-read`), live-scores, and a FedEx Cup season simulation are all deployed and active in the frontend -- see the `project-pga-onboarding` and `project-pga-fedex-cup-simulation` memories for build history.

Code lives in four places:
- `Source/library/features/pga.py` -- pure feature-computation functions (no AWS calls). `rolling_golfer_averages`/`build_golfer_event_features` (tournament grain), `rolling_round_averages`/`build_round_event_features` (round grain), `build_cutline_event_features` (tournament grain, no golfer dimension).
- `Source/library/normalize/pga.py` -- where `purse`/`is_major`/`cut_score`/`cut_round`/`cut_count`/`course_id` (this doc's own field-strength/cut-line inputs) and `rounds` (per-round results) actually get parsed off the raw ESPN response, onto the `events` table item/participant result itself, not onto anything feature-engineering-specific.
- `Source/library/http/pga.py` -- `PGAClient.get_statistics`, the season-stats snapshot fetch (see "Season stats" below).
- `Source/feature-engineering/pga/build_dataset.py` -- the Fargate entrypoint that walks DynamoDB history (via `FeatureStorage`) plus raw season-stats snapshots (via a raw-bucket `S3Manager`), calls the functions above, and writes THREE Parquet files to S3.

**This is not a scaled-down copy of NBA's/NFL's feature set** -- PGA is the first field-event sport (see `design/DATA_SCHEMA.md`'s two event shapes), and the differences below aren't missing pieces, they're the shape a sport with no opponent, no team, and no separate box-score table actually has.

## Pipeline

```
DynamoDB (entities, events)                    raw data lake (pga/statistics/{date}.json)
        |  FeatureStorage (read-only)                    |  S3Manager (read-only)
        v                                                 v
                feature-engineering/pga/build_dataset.py
        |  build_golfer_dataset()   -> golfer_features.parquet   (also joins season-stats snapshots)
        |  build_round_dataset()    -> round_features.parquet
        |  build_cutline_dataset()  -> cutline_features.parquet
        v
S3: s3://<model-artifacts-bucket>/pga/training-data/*.parquet
        v
model-training/pga/train_{top10,top5,score,cutline,round}_model.py (reads its own Parquet, trains, versions an artifact)
```

Not scheduled -- run manually via `aws ecs run-task` or as the first step of the training Step Function (`sfn-training-orchestrator.tf`'s `RunFeatureEngineering` state), driven by PGA's registry row (`Terraform/dynamodb-sport-registry.tf`'s `pga_registry`, whose `training_targets` now lists all 8 targets below -- top-10, top-5, score, cutline, and rounds 1-4). Safe to re-run any time -- it always rebuilds all three datasets from whatever's currently in DynamoDB/the raw bucket and overwrites the same three S3 keys; there's no incremental state.

## The three datasets

No separate event-level win/loss dataset the way every head-to-head sport builds one -- a field event has no home/away side to build Elo ratings or a win/loss label from. No player-game-stats-style dataset either -- there's no `player_game_stats` table for a field-event sport at all (`design/DATA_SCHEMA.md`): a golfer's own per-tournament performance already lives directly in `events.participants`.

| Dataset | Grain | One row = | Built by |
|---|---|---|---|
| `golfer_features.parquet` | golfer-tournament | one golfer's result in one completed tournament | `build_golfer_dataset` + `build_golfer_event_features` |
| `round_features.parquet` | golfer-tournament-round | one golfer's result in one round they actually played | `build_round_dataset` + `build_round_event_features` |
| `cutline_features.parquet` | tournament | one completed tournament, no golfer dimension at all | `build_cutline_dataset` + `build_cutline_event_features` |

All three are built by walking every completed PGA tournament in chronological order exactly once, growing the relevant history incrementally as the walk proceeds. Same label-leakage discipline every other sport's rolling-average feature builder enforces, with one extra ordering rule specific to a many-entrant field: **every row for a given tournament is built from the SAME snapshot of history, before ANY of that tournament's own results are folded into anyone's history.** A 150-golfer field means 150 rows share one tournament's `event_date` -- if history updates were interleaved participant-by-participant instead, a golfer processed later in the same tournament could see an earlier golfer's just-computed result as if it were prior history, which is wrong for a field where every entrant tees off the same week. Each of the three `build_*_dataset` functions enforces this with two separate loops per tournament (build every row first, then update history), not one combined loop.

## Shared primitives

**None of `library/features/common.py` apply here.** Every one of its functions -- `compute_elo_ratings`, `rolling_team_scoring_averages`, `current_streak`, `average_opponent_elo` -- is built around a `home`/`away` participant pair and an opponent to rate strength-of-schedule against. A field event has neither: there's no opponent, so there's no Elo to compute (a golfer isn't rated relative to one other entrant, but against however many dozens showed up that week), and no `role` key on a `participants` entry at all (`design/DATA_SCHEMA.md`'s field-event `participants` section). `library/features/pga.py` is a clean-room set of rolling-history helpers for exactly this shape, not an adaptation of the shared ones.

- **Rolling golfer averages** (`rolling_golfer_averages`) -- this golfer's own `score_to_par`, `finish_position`, `earnings`, top-10/top-20 rate, and finish rate, averaged over up to the last `window` starts (default 5, same `DEFAULT_ROLLING_WINDOW` convention every sport uses).
- **Rolling round averages** (`rolling_round_averages`) -- added 2026-08-25 for round-level modeling. This golfer's own `score_to_par` specifically at the SAME round number (e.g. every past "round 1" they've played across tournaments), not their overall average -- a genuinely different signal (fast/slow starters, strong/weak closers). Round dicts have no `finish_position`/`earnings` at all (a round-grain concept, not a tournament-grain one), which is why this is its own function rather than a reuse of `rolling_golfer_averages` against round dicts.

## `golfer_features.parquet` fields

One row per golfer per completed tournament **per in-tournament-progress snapshot** (added 2026-08-27; see "In-tournament progress" below) -- a golfer who played all 4 rounds contributes 5 rows (0 rounds played through 4), not 1; a cut golfer contributes 3 (0 through 2). Every snapshot for the same (golfer, tournament) shares the identical `label_*` columns -- only `rounds_completed_this_week`/`score_to_par_this_week_so_far` differ between them.

| Field | Description |
|---|---|
| `event_key`, `entity_id`, `event_date` | Identifiers -- excluded from model inputs by every training script that reads this dataset (`train_top10_model.py`, `train_top5_model.py`, `train_score_model.py`). |
| `purse`, `is_major` | This tournament's own field-strength context -- see "Field strength" below. |
| `field_size` | This tournament's own participant count (`len(event["participants"])`) -- a per-event feature, not a rolling average (see "Field strength"). |
| `rounds_completed_this_week`, `score_to_par_this_week_so_far` | How this golfer is doing in THIS tournament, right now -- see "In-tournament progress" below. `0`/`None` for the pre-tournament snapshot (and for every row built before 2026-08-27, back-filled the same way on next rebuild). |
| `avg_score_to_par` | Rolling average, over starts that have a value -- a missed-cut golfer still contributes the rounds they actually played. |
| `avg_finish_position`, `best_finish_position` | Rolling average/min, over starts that resolved to a real finish (`finish_position is not None`) -- a missed cut, withdrawal, or disqualification contributes to `finish_rate`'s denominator (below) but not to these two. |
| `top_10_rate`, `top_20_rate`, `finish_rate` | Rolling rate, divided by STARTS in the window, not just finishes -- missing the cut is a real outcome that counts against making top 10, not a row silently excluded from the denominator. This is the one place a naive port of `rolling_player_stat_averages`' "only average what's present" convention would have quietly been wrong: `avg_score_to_par`/`avg_finish_position` genuinely should skip absent values, but a *rate* has to include every start in its denominator or it overstates how good a golfer's recent form actually was. |
| `avg_earnings` | Rolling average, over starts with a value (`0` for a missed cut is itself a value, not absent -- see `design/DATA_SCHEMA.md`). |
| `events_played` | How much rolling history backs the averages above -- lets the model discount a golfer with little recent data the same way `games_played`/`starts` does for every other sport's player-prop rows. |
| `course_avg_score_to_par`, `course_avg_finish_position`, `course_best_finish_position`, `course_top_10_rate`, `course_top_20_rate`, `course_finish_rate`, `course_avg_earnings`, `course_events_played` | This golfer's own history specifically at THIS tournament's `course_id` -- see "Course fit" below. Same eight fields as the overall rolling block, `course_`-prefixed, computed by the exact same `rolling_golfer_averages` call against a course-scoped history list instead of the golfer's whole history. |
| `season_driving_distance`, `season_driving_accuracy_pct`, `season_gir_pct`, `season_putts_per_hole`, `season_birdies_per_round`, `season_scoring_average` | This golfer's own season-to-date stats as of the most recent snapshot before this event -- see "Season stats" below. `None` for 100% of backfilled historical rows (there is no historical source for this data at all), and for any golfer who wasn't in that category's top 50 that day. |
| `label_top_10` | Training label -- `1` if `finish_position is not None and finish_position <= 10`, else `0` (a missed cut, a finish outside the top 10, and a withdrawal/DQ all correctly resolve to `0` from this one condition, no separate per-status branch needed). |
| `label_top_5` | Same shape as `label_top_10`, threshold 5 -- trained by `train_top5_model.py`. Both labels live in this one dataset; there's no separate top-5-specific Parquet build. |
| `label_remaining_score_to_par` | This golfer's own final `score_to_par` for the tournament, MINUS `score_to_par_this_week_so_far` (0 pre-tournament) -- the continuous label `train_score_model.py` trains on. Framed as the REMAINING score, not the absolute final, so the model can't just learn to echo its own current-cumulative feature back (see "In-tournament progress" below); serving time adds the real cumulative back on to get the field-facing `projected_score_to_par`. `None` for a golfer with no recorded final score at all (e.g. a withdrawal before playing a single hole), filtered out at train time. |

## `round_features.parquet` fields

One row per golfer per round ACTUALLY PLAYED (`participants[].result.rounds` -- `design/DATA_SCHEMA.md`). A cut golfer's own `rounds` naturally has only 2 entries, not 4, so this dataset simply has no round-3/4 row for them at all -- no conditional cut-logic needed anywhere that builds or reads it.

| Field | Description |
|---|---|
| `event_key`, `entity_id`, `event_date` | Identifiers -- excluded from model inputs by `train_round_model.py`. |
| `round_number` | 1-4. Also excluded from model inputs -- `train_round_model.py` filters the whole dataset down to one round number per training run (`ROUND_NUMBER` env var), so it's a constant column by the time features are built, not something to split on. |
| `purse`, `is_major`, `field_size` | Same per-tournament context as `golfer_features.parquet` -- see "Field strength" above. |
| `overall_avg_score_to_par`, `overall_avg_finish_position`, `overall_best_finish_position`, `overall_top_10_rate`, `overall_top_20_rate`, `overall_finish_rate`, `overall_avg_earnings`, `overall_events_played` | This golfer's usual TOURNAMENT-level rolling form (`rolling_golfer_averages`, `overall_`-prefixed) -- the same signal `golfer_features.parquet` carries unprefixed. |
| `same_round_avg_score_to_par`, `same_round_rounds_played` | This golfer's own history specifically at THIS round number across past tournaments (`rolling_round_averages`, `same_round_`-prefixed) -- see "Shared primitives" above. |
| `label_round_score_to_par` | This round's own actual `score_to_par` -- the continuous label. |

Deliberately scoped smaller than `golfer_features.parquet` for this first version: no course-fit or season-stats block here yet -- add them later the same way course fit was added to the tournament-level dataset, once round-level modeling proves out.

## `cutline_features.parquet` fields

One row per completed Medal-scoring tournament -- **no golfer dimension at all**, since a cut line is a property of the whole field, not any one golfer's own result. Includes every tournament, cut or not; `train_cutline_model.py` filters to `cut_count > 0` at train time (a no-cut tournament genuinely reports `cut_count=0`, not a missing value -- `design/DATA_SCHEMA.md`).

| Field | Description |
|---|---|
| `event_key`, `event_date` | Identifiers -- excluded from model inputs. |
| `purse`, `is_major`, `field_size` | Same per-tournament context as `golfer_features.parquet`. |
| `course_avg_cut_score` | This SAME course's own past `cut_score` values, averaged over up to `COURSE_HISTORY_WINDOW` appearances -- a course that plays hard or easy tends to do so consistently year to year, the one rolling signal that makes sense at this grain. |
| `cut_count` | How many players made the cut -- kept on the dataset for the train-time filter above, excluded from model inputs (a live prediction can't know this in advance; it's a *result*, not a pre-tournament feature). |
| `label_cut_score` | This tournament's own actual `cut_score` -- the continuous label. |

## Season stats

Added 2026-08-25, in response to a real user ask ("do we have access to statistics like putting, fairways hit, greens hit ... computed both event-to-event and across the season"). Investigated directly against live ESPN data first, per this project's own "verify before building" discipline:

- **No per-golfer-per-event breakdown exists anywhere.** A tournament's `competition.leaders` block only names the SINGLE category leader (one golfer's number per category, not the whole field's); every competitor's own `statistics` array is limited to `scoreToPar`/`officialAmount`/`cupPoints`. There is no way to build an event-to-event putting/fairways/GIR/driving-distance feature from anything this project's existing leaderboard fetch returns -- not built, not buildable from this data source at all.
- **A separate endpoint DOES expose season-to-date versions**, `site.web.api.espn.com/apis/site/v2/sports/golf/pga/statistics` (`PGAClient.get_statistics`) -- driving distance/accuracy, GIR%, putts per hole, birdies per round, scoring average, top 50 players per category. Confirmed live that its `season`/`year` query parameters are silently ignored (byte-identical response regardless of value passed), so it is **CURRENT-SNAPSHOT-ONLY** -- there is no way to retrieve a past season's value retroactively.
- **`aws-lambdas/pga/ingest/handler.py` now captures a daily snapshot** (`pga/statistics/{date}.json`, unconditionally every run, regardless of whether a tournament is current) specifically because this is the ONLY way this project can ever build historical values for these categories -- every day without a captured snapshot is a day of history permanently lost. This means the season-stats columns will be **entirely `None` for the whole existing backfill** (2017-2026, since capture only started once this shipped) and will only gradually fill in going forward as snapshots accumulate.
- **`feature-engineering/pga/build_dataset.py`'s `_load_season_stat_snapshots`/`_resolve_season_stats`** read these raw snapshots directly from the raw data lake bucket (the same "read a raw non-DynamoDB S3 prefix at feature-engineering time" pattern NCAA MBB's own AP-poll-based ranking dataset uses) and resolve each golfer's own values from the most recent snapshot STRICTLY BEFORE the tournament's `event_date` -- never same-day-or-later, which would leak in-progress state.
- Only 6 of the ~12 categories this endpoint returns are carried into the dataset (`library.features.pga.SEASON_STAT_CATEGORIES`) -- `officialAmount`/`cupPoints`/`wins`/`topTenFinishes`/`cutsMade` are deliberately excluded, since this project already computes its own close equivalents from event history (`avg_earnings`, `top_10_rate`, `finish_rate`); ESPN's own season-long versions of those would be redundant, not new signal.

## Field strength

A field event has no opponent to build a strength-of-schedule signal from the way `average_opponent_elo` does for a head-to-head sport -- there's no single "opponent," just however many entrants showed up. Three fields stand in for that signal instead, all **per-tournament, not rolling** (known ahead of the tournament, so they're equally available at live-prediction time, not just training time, and a rolling average across them wouldn't mean anything the way a rolling Elo does):

- **`purse`** -- the tournament's own prize money (`library/normalize/pga.py`'s `leaderboard_event_to_event_item`, sourced from ESPN's own top-level `purse` field, confirmed live 2026-08-24). A bigger purse tends to draw a stronger field.
- **`is_major`** -- whether this is one of the four major championships (ESPN's `tournament.major` flag). The strongest fields of the year play in majors regardless of purse size.
- **`field_size`** -- this tournament's own entrant count. A bigger field mechanically lowers everyone's odds of a top-10 finish regardless of who's in it, independent of how strong the field is.

Both `purse` and `is_major` were added to the `events` table item specifically because this model needed them and no per-golfer rolling average could substitute -- a career-best round in a weak-field regular event says less than the same round shot in a major, and no amount of that golfer's own history changes that. See `design/DATA_SCHEMA.md`'s own note on why these are PGA-only fields with no head-to-head equivalent.

## Course fit

Added 2026-08-25, in response to a real user ask ("can we also add course fit features"). `library/features/pga.py`'s `rolling_golfer_averages` needed no changes at all to support this -- it already operates on a plain list of a golfer's own past `result` dicts; `build_golfer_event_features` just calls it a second time against a COURSE-SCOPED history list (this golfer's own past results specifically at `course_id`, most recent first, capped at `DEFAULT_COURSE_HISTORY_WINDOW` appearances -- default 5) instead of the overall one, and prefixes every resulting key with `course_`.

`course_id` is the host course's own ESPN id (`courses[].id`, e.g. `"65"` for Bellerive Country Club), added to the `events` table item alongside `venue_name` specifically for this -- see `design/DATA_SCHEMA.md`'s own note on why a stable id, not the course name string, is what a multi-season course-fit history needs to key on.

`DEFAULT_COURSE_HISTORY_WINDOW` (5) means something genuinely different from `DEFAULT_ROLLING_WINDOW` (5) despite the same number: a course only recurs on tour roughly once a year, so "last 5 course appearances" spans roughly the golfer's last 5 YEARS at that course, not the last 5 weeks the overall rolling window covers. `feature-engineering/pga/build_dataset.py`'s `build_golfer_dataset` tracks this as a second, separate incremental history dict keyed by `(entity_id, course_id)`, alongside the existing per-golfer-only one -- same two-loops-per-tournament ordering discipline (build every row from the current snapshot, THEN fold this tournament's results into both histories) applies to both.

An event with no `course_id` at all (should only happen for raw data captured before this shipped) contributes to no course history and gets `course_events_played=0`/every other `course_*` field `null` for its own row -- it still gets full treatment on every non-course-fit field.

## In-tournament progress

Added 2026-08-27, fixing a real user-reported bug: `library/storage/prediction_cache.py`'s `rounds_fingerprint`-triggered recompute correctly re-ran `projected_score_to_par`/`top_10_probability`/`top_5_probability` the moment round 1's real results landed, but the OUTPUT never changed -- every other input to `build_golfer_event_features` (rolling form, course fit, season stats) is a snapshot of history strictly BEFORE this tournament, so a recompute mid-tournament reproduced the exact same feature vector as the pre-tournament one. The model had no way to know a golfer had just shot a 65.

Two new fields (`library/features/pga.py`'s `in_tournament_progress_features`) close that gap: `rounds_completed_this_week` (0-4) and `score_to_par_this_week_so_far` (sum of this golfer's own already-played rounds' `score_to_par`, `None` before any are played). At serving time (`aws-lambdas/pga/predict/live_features.py`) these come straight from the golfer's own current `participant["result"]["rounds"]` -- live, real, updates every normalize/live-scores cycle.

Training this signal needed more than just adding two columns: a dataset built entirely from PRE-tournament rows (`rounds_completed_this_week` always 0) would never let the model learn to use it, no matter how predictive it might be at serving time. So `build_golfer_dataset` now emits one snapshot row PER ROUND BOUNDARY this golfer actually reached that week -- 0 rounds played (the original pre-tournament row every golfer has always had), 1, 2, and so on up to however many rounds they played -- all predicting toward the SAME real final outcome. `label_top_10`/`label_top_5` share one identical value across every snapshot; `label_remaining_score_to_par` instead shrinks per snapshot (final minus whatever's already been shot) -- see the field's own table entry above for why it's framed that way, not as the identical final value repeated. This multiplies `golfer_features.parquet`'s own row count (roughly by average rounds-played-per-golfer + 1) but needs no schema change anywhere else -- `train_score_model.py`/`train_top10_model.py`/`train_top5_model.py` pick up any new column automatically (`library/ml/training_common.py`'s `feature_columns` is dynamic, not an explicit allowlist).

**Retrained 2026-08-28** after a real user report (`projected_score_to_par` looked like the current round's own cumulative, not a full-tournament projection) traced to exactly this: the FIRST retrain after `rounds_completed_this_week`/`score_to_par_this_week_so_far` existed still trained toward the absolute final score, and `score_to_par_this_week_so_far` (confirmed via that model's own `feature_importances`) came out ~2.7x more important than every other feature, effectively collapsing predictions to "final ≈ current cumulative." Reframing the label to the REMAINING score (above) forces the model to actually reason about the rounds still ahead instead of being able to just echo its own leaking current-state feature back.

This is a per-golfer-per-tournament in-progress signal, not a rolling history one -- it's never folded into `history`/`course_history` (the incremental dicts `build_golfer_dataset` grows tournament-by-tournament); a later tournament's own rolling averages still only ever see this golfer's real FINAL result for this one, the same as before this feature existed.

## Why a top-10 classifier, not a win/loss classifier or a genuine multinomial model

A ranking-style model (multinomial classification, or top-N probability) was the goal, rather than reusing a plain win/loss classifier. Both alternatives were considered:

- **Plain win/loss** was rejected outright -- it's explicitly what the checklist says to avoid, and for good reason: winning is roughly a 1-in-100+ event in a full field, an extreme class-imbalance problem that would barely be learnable and wouldn't say much about a golfer's real form the way "top 10" does.
- **A genuine multinomial (softmax) model** over ordered finish tiers (win / top 5 / top 10 / top 20 / made cut / missed cut) was the more literal reading of "multinomial classification," but **no multi-class task type exists anywhere in `library/ml/backtest.py`/`library/ml/model_types.py` today** -- every classifier adapter hardcodes a binary objective (`objective="binary:logistic"`, `predict_proba(X)[:, 1]`, etc.). Building real multi-class support would mean extending shared training infrastructure every other sport's models also depend on, for a checklist item that names an equally valid, much lower-risk alternative right in the same sentence.
- **Top-10 finish probability** (`top-10-probability`) is that alternative -- a genuine binary classification target, trained through the exact same `library.ml.backtest.run_backtest` harness and the same 5-candidate (XGBoost/logistic regression/random forest/MLP/LightGBM) tournament every other sport's flagship classifier uses, with zero changes to shared code. Still a real, useful, and genuinely different shape from win/loss: top 10 is a common, meaningfully rankable outcome (roughly 10 out of 100+ entrants every week), not a rare event.

## Naive baseline -- why it can't copy win-probability's formula verbatim

Every head-to-head sport's win-probability model computes its naive baseline as `y_test.mean()` (the holdout's own positive-class rate) -- that works there specifically because "home team wins" happens to already be the majority class in real NFL/NBA/etc. data, so predicting "home always wins" already approximates the majority-class guess. Top-10 is a small-minority label (roughly 10 positive rows out of every 100+), so using `y_test.mean()` directly would silently report the *minority*-class rate as if it were the trivial baseline. `train_top10_model.py` instead uses `max(y_test.mean(), 1 - y_test.mean())` -- whichever class is actually the majority in that holdout -- a generically correct formula, not a PGA-specific hardcode.

## What's deliberately absent

- **No hole-by-hole features.** ESPN's leaderboard response carries a further-nested hole-by-hole breakdown within each round, on top of the round-level `linescores` this project DOES now use (see `design/DATA_SCHEMA.md`'s `rounds` field) -- confirmed live that the hole-level nesting specifically is not consistently populated across every fetch, unlike the round level (100% populated, two real tournaments checked). Not parsed, not planned -- round-level granularity is already finer than anything a head-to-head sport's own box score gets.
- **No amateur/country features.** `entities.metadata.country`/`metadata.amateur` (`library/normalize/pga.py`'s `leaderboard_event_to_player_entities`) are entity-level display fields, not folded into the training row -- an amateur's rare PGA Tour start is too sparse a signal to be worth a feature column yet.
- **No Data Golf integration.** Considered and skipped in step 2 (`project-pga-onboarding` memory) -- ESPN alone is sufficient for what this model needs, no gap Data Golf's own skill ratings would fill yet.

## Team/match-play tournaments are excluded entirely

Found + fixed 2026-08-25, during a pre-backfill review: PGA TOUR's real calendar includes several genuinely different tournament formats (Ryder Cup, Presidents Cup, WGC-Dell Technologies Match Play -- team or individual match play; Zurich Classic of New Orleans -- team stroke play; The Match -- exhibition) that this project's schema and normalizers were never built for. Confirmed live, reproduced directly against real API responses, that feeding any of these through the normalizer crashes it (an `AttributeError` from a genuinely different `competitions` shape, or a `KeyError` from a missing `athlete` key on team-based competitors). `library/normalize/pga.py`'s `is_medal_scoring(event)` -- checking ESPN's own `tournament.scoringSystem.name == "Medal"` -- is the fix, checked by every caller (ingest, schedule-sync, normalize, backfill) before ever normalizing an event, with both normalizer functions also raising `ValueError` up front as a defense-in-depth backstop. None of these tournaments' results ever reach any of the three datasets above, or the `events` table at all -- see `design/DATA_SCHEMA.md`'s own note.

## Models trained on these features

`Source/model-training/pga/` -- same shared plumbing as every other sport (`library/ml/training_common.py`, `library/ml/backtest.py`), all seven scripts sharing one Docker image (`model-training/pga/Dockerfile`), same shared-image/command-override pattern NBA's own train-win-probability-model image uses.

| Predicted value | Model type(s) trained & evaluated | Training script | Dataset |
|---|---|---|---|
| Top-10 finish probability (`label_top_10`) | 5-way classifier tournament (XGBoost, logistic regression, random forest, MLP, LightGBM) on `log_loss` | `train_top10_model.py` | `golfer_features.parquet` |
| Top-5 finish probability (`label_top_5`) | Same 5-way classifier tournament | `train_top5_model.py` | `golfer_features.parquet` |
| Remaining score-to-par (`label_remaining_score_to_par`) -- served as `projected_score_to_par` (the real cumulative-so-far added back on, see `event_prediction.py`'s `_field_projected_score_to_par`) and the basis for "field finish order" (a serving-time ranking of that served value, not a separately trained artifact -- see below) | 4-way regressor tournament (XGBoost, ElasticNet, random forest, MLP) on `rmse` | `train_score_model.py` | `golfer_features.parquet` |
| Projected cut line (`label_cut_score`), tournament grain | Same 4-way regressor tournament, filtered to `cut_count > 0` | `train_cutline_model.py` | `cutline_features.parquet` |
| Per-round projected score (`label_round_score_to_par`), one model per round | Same 4-way regressor tournament, one model per `ROUND_NUMBER` (1-4), filtered to that round | `train_round_model.py` (`ROUND_NUMBER` env var) | `round_features.parquet` |
| Cup (team) win probability (`label_home_won`), Ryder Cup/Presidents Cup only | Same 5-way classifier tournament | `train_cup_winprob_model.py` | `cup_features.parquet` |
| Individual match win probability (`label_home_won`), one row per foursomes/fourball/singles match or WGC bracket match | Same 5-way classifier tournament | `train_match_winprob_model.py` | `match_features.parquet` |

### Field finish order

Not a separately trained model -- this project's shared training harness (`library/ml/backtest.py`) has no rank-loss/learning-to-rank objective, and building one would mean extending shared infrastructure every other sport's models also depend on (the same reasoning that kept top-10/top-5 as plain binary classifiers instead of a genuine multinomial model). Instead, a live prediction request scores every golfer in a tournament's field through `projected-score-to-par` and ranks the field by predicted value, lowest score first -- a serving-time transformation of an existing regression model's output, not a new artifact.

### Projected cut line and skipping rounds 3-4

`train_cutline_model.py` trains the cut-line NUMBER; `train_round_model.py` trains each round's own score independently, with rounds 3-4 naturally having fewer training rows already (a cut golfer's own `rounds` list simply has no round-3/4 entry -- no special-casing needed at training time, see `round_features.parquet`'s own section above). The predict Lambda compares a golfer's own projected 36-hole cumulative score against `projected-cut-line`'s output before ever calling the round-3/4 models for them, so a projected-cut golfer's own "This Rd" display shows their real round-4 predicted-vs-actual rather than a simulated round it never expected them to play.

### Cup and match win probability

Two more binary classifiers, added after the original 5: `cup_features.parquet` (one row per Ryder Cup/Presidents Cup, home-team vs. away-team rolling stroke-play form averaged across each team's full roster) and `match_features.parquet` (one row per individual match -- a foursomes/fourball/singles pairing, or a WGC-Dell Technologies Match Play bracket match). Both reuse `label_home_won` as the target and the same 5-way classifier tournament as top-10/top-5. `cup_features.parquet` is genuinely small -- only Ryder Cup/Presidents Cup editions since 2017 (WGC Match Play has no Cup-level row at all) -- so its promoted model's metrics warrant more skepticism than the other 6's.

## What this makes it possible to predict

A golfer's probability of finishing in the top 10 or top 5 of a given tournament; their projected score-to-par (and, derived from it at serving time, their implied position in the field); a tournament's own projected cut line; a golfer's own projected score for any round they're expected to play; a team's win probability in the Ryder Cup/Presidents Cup; and an individual match's win probability (Ryder Cup/Presidents Cup sessions, or a WGC Match Play bracket match). All served live by `Source/aws-lambdas/pga/predict/`, plus a separate FedEx Cup season-long simulation (see the `project-pga-fedex-cup-simulation` memory).

**Not built:** finish-position regression (a genuine multinomial/ranked-finish model, superseded by the field-finish-order serving-time ranking above) and any second field-event sport's reuse of this pattern (F1 has its own parallel model set, not a reuse of PGA's -- see `docs/F1_FEATURE_ENGINEERING.md`).
