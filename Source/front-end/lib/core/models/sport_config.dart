import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// Determines UI shape, not data shape -- the `participants` array is the
/// same structure either way. h2h shows win probability + margin; field
/// events show a finishing-position distribution.
enum EventShape { headToHead, field }

/// One entry per sport this app knows about, active or not. Adding a new
/// sport to the backend means flipping `active: true` here -- no new
/// widgets, no new repository code, since every sport hits the same
/// `/{sport}/events`, `/{sport}/predictions/...`, `/{sport}/models` route
/// shapes.
class SportConfig {
  const SportConfig({
    required this.id,
    required this.displayName,
    required this.eventShape,
    required this.accentColor,
    required this.active,
    this.hasSeasonProjection = true,
    this.usesFedexCupSeasonPage = false,
  });

  final String id;
  final String displayName;
  final EventShape eventShape;
  final Color accentColor;
  final bool active;

  // False hides sport_shell_page.dart's Season tab entirely -- for a sport
  // whose backend has no /{sport}/season route, GET-ing it would just
  // surface a raw "couldn't load" error on every tap. Defaults true so
  // most entries below need no change.
  final bool hasSeasonProjection;

  // True routes /{sport}/season to PgaSeasonPage (a points-standings
  // table -- FedEx Cup, no bracket) instead of the shared bracket-based
  // SeasonPage every EventShape.headToHead sport uses. A separate flag
  // from eventShape deliberately -- F1 is also EventShape.field but
  // (once active) will want its own driver-standings shape, not
  // necessarily this one; overloading eventShape would force them to
  // match.
  final bool usesFedexCupSeasonPage;
}

const kSports = [
  SportConfig(
    id: 'nfl',
    displayName: 'NFL',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    // Matches the backend's own route prefix (/ncaafb/...) exactly --
    // this id is passed straight through as the API path segment (see
    // app_router.dart's :sport param), so it can't diverge from it.
    id: 'ncaafb',
    displayName: 'NCAA Football',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    id: 'nba',
    displayName: 'NBA',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    // Matches the backend's own route prefix (/ncaambb/...) exactly, same
    // reasoning as ncaafb's id above.
    id: 'ncaambb',
    displayName: 'NCAA MBB',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    id: 'pga',
    displayName: 'PGA Tour',
    eventShape: EventShape.field,
    accentColor: AppColors.violet,
    active: true,
    // FedEx Cup season simulation (aws-lambdas/pga/predict/
    // season_projection.py) -- a points-standings table, not a bracket,
    // hence usesFedexCupSeasonPage routing to its own PgaSeasonPage.
    hasSeasonProjection: true,
    usesFedexCupSeasonPage: true,
  ),
  SportConfig(
    id: 'f1',
    displayName: 'Formula 1',
    eventShape: EventShape.field,
    accentColor: AppColors.violet,
    active: false,
  ),
];

SportConfig sportById(String id) => kSports.firstWhere((s) => s.id == id);
