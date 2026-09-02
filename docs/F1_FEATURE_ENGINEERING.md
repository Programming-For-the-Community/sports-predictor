# F1 Feature Engineering

**Status: fully built and live.** Feature engineering, training (9 models across 3 datasets), inference (`predict`/`predict-read`), live-scores, and a driver/constructor championship season simulation are all deployed and active in the frontend -- see the `project-f1-onboarding` memory for build history.

Describes what F1's model-training features are, how they're computed, and where each field comes from. Covers the same ground `design/DATA_SCHEMA.md` covers for storage -- this doc is specifically about the derived training data, not the raw DynamoDB/S3 schema it's built from.

Code lives in three places:
- `Source/library/features/f1.py` -- pure feature-computation functions (no AWS calls): `build_driver_event_features`, `build_constructor_event_features`, `build_sprint_event_features`.
- `Source/library/features/f1_points.py` -- constructor points as a real sum of both drivers' points each race.
- `Source/feature-engineering/f1/build_dataset.py` -- the Fargate entrypoint that walks DynamoDB history (via `FeatureStorage`), calls the functions above, and writes three Parquet files to S3: `driver_features.parquet`, `constructor_features.parquet`, `sprint_features.parquet`.

## Why F1 mirrors PGA's shape, not a head-to-head sport's

F1 is the second field-event sport onboarded (`design/DATA_SCHEMA.md`'s field-event `participants` shape) -- a driver's own entity IS the participant, same as a golfer, so there's no separate `player_game_stats` table and no opponent concept for a driver's own rolling history. Two blocks have no PGA analog at all, though:

- **Constructor (team) rolling form** -- a driver's result depends heavily on car competitiveness, a signal that belongs to the constructor, not the driver. `build_constructor_event_features` sums (not averages) both of a constructor's drivers' rolling form.
- **Rolling qualifying pace** -- a genuinely different skill signal from race-day rolling form, tracked as its own rolling block.

**Sprint races get their own feature builder and their own separate rolling history** (`build_sprint_event_features`), not folded into `rolling_driver_averages`' main-race form -- a Sprint weekend writes the Saturday sprint and Sunday Grand Prix as two separate events (`library/normalize/f1.py`'s `sprint_result_to_event_item`), and blending their form would conflate two genuinely different race formats (shorter, no pit strategy, different points).

## Rolling driver form (`rolling_driver_averages`)

Computed over a driver's own past `participants[].result` rows, most recent first, not including the race being scored. `podium_rate`/`top_10_rate`/`dnf_rate` are divided by the number of starts in the window, not just classified finishes -- a DNF counts against making the podium, not excluded from the denominator. `avg_finish_position`/`avg_grid_position`/`avg_points` average only over rows with a real value (`finish_position` is `None` for a non-classified result, same "`None` means no real value, not 0" discipline `library/features/pga.py`'s `rolling_golfer_averages` uses). Every value is `None`, not 0, when the window has no qualifying rows.

**Circuit-fit history** (`DEFAULT_CIRCUIT_HISTORY_WINDOW = 5`) is capped by count of past appearances at that circuit, not calendar recency -- a circuit recurs roughly once a year, so 5 here means "last 5 years at this circuit," a longer real span than `DEFAULT_ROLLING_WINDOW`'s "last 5 starts." Kept as its own constant, tuned independently, same precedent as PGA's `DEFAULT_COURSE_HISTORY_WINDOW`.

## Models trained on these features

`Source/model-training/f1/` -- same shared plumbing as every other sport (`library/ml/training_common.py`, `library/ml/backtest.py`), all nine scripts sharing one Docker image.

| Predicted value | Model type(s) trained & evaluated | Training script | Dataset |
|---|---|---|---|
| Race win probability (`label_win`) | 5-way classifier tournament (XGBoost, logistic regression, random forest, MLP, LightGBM) on `log_loss` | `train_winprob_model.py` | `driver_features.parquet` |
| Podium (top-3) probability (`label_podium`) | Same 5-way classifier tournament | `train_podium_model.py` | `driver_features.parquet` |
| Projected finish position | 4-way regressor tournament (XGBoost, ElasticNet, random forest, MLP) on `rmse` -- the basis for "field finish order," a serving-time ranking of this model's own output, same role PGA's `train_score_model.py` plays | `train_finish_position_model.py` | `driver_features.parquet` |
| DNF probability (`label_dnf`) | Same 5-way classifier tournament -- no PGA analog; a binary "did this driver's own race end early" outcome | `train_dnf_model.py` | `driver_features.parquet` |
| Projected qualifying position (`label_qualifying_position`, the real qualifying-session classification position from Jolpica's own `qualifying.json`) | Same 4-way regressor tournament | `train_qualifying_model.py` | `driver_features.parquet` |
| Constructor (team) race win probability (`label_win`, 1 if EITHER driver won) | Same 5-way classifier tournament, one row per constructor per race, both drivers' rolling form summed as features | `train_constructor_winprob_model.py` | `constructor_features.parquet` |
| Sprint race win probability | Same 5-way classifier tournament, trained on its own separate rolling history | `train_sprint_winprob_model.py` | `sprint_features.parquet` |
| Sprint podium probability | Same 5-way classifier tournament, own separate rolling history | `train_sprint_podium_model.py` | `sprint_features.parquet` |
| Projected Sprint starting grid position | Same 4-way regressor tournament -- the closest available "Sprint qualifying" model; Jolpica has no separate Sprint Qualifying/Sprint Shootout results endpoint at all, confirmed live | `train_sprint_grid_model.py` | `sprint_features.parquet` |

## What this makes it possible to predict

A driver's win, podium, and DNF probability for a race; their projected finish position (and, derived from it at serving time, the field's own implied running order); their projected qualifying position; a constructor's own race win probability; and the Sprint-race analogs of win/podium/starting-grid for a Sprint weekend. All served live by `Source/aws-lambdas/f1/predict/`, plus a separate driver/constructor championship season simulation (`season_simulation.py`, Monte Carlo over the remaining calendar, same approach as NFL/NBA's own season simulations) and a weekly season-projection precompute (`season_projection.py`, `Terraform/scheduler-f1-season-projection.tf`).

**Not built:** a genuine multinomial/full-grid-ranking model (superseded by the field-finish-order serving-time ranking above, same reasoning as PGA's).
