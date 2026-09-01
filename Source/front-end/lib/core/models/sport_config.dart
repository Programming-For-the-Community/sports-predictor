import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// Determines UI shape, not data shape -- the `participants` array is the
/// same structure either way. h2h shows win probability + margin; field
/// events show a finishing-position distribution.
enum EventShape { headToHead, field }

/// Every sport id this app knows about -- also the literal backend API
/// path segment (see SportConfig.id's own doc comment), so these values
/// can never diverge from what the backend routes on. The one place a
/// sport id is ever spelled out as a string; every other file compares
/// against these constants instead of retyping the literal.
abstract final class SportIds {
  static const nfl = 'nfl';
  static const ncaafb = 'ncaafb';
  static const nba = 'nba';
  static const ncaambb = 'ncaambb';
  static const pga = 'pga';
  static const f1 = 'f1';
}

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
  // table, no bracket) instead of the shared bracket-based SeasonPage.
  // Separate from eventShape since F1 is also EventShape.field but will
  // want its own driver-standings shape, not necessarily this one.
  final bool usesFedexCupSeasonPage;
}

const kSports = [
  SportConfig(
    id: SportIds.nfl,
    displayName: 'NFL',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    // Matches the backend's own route prefix (/ncaafb/...) exactly --
    // this id is passed straight through as the API path segment (see
    // app_router.dart's :sport param), so it can't diverge from it.
    id: SportIds.ncaafb,
    displayName: 'NCAA Football',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    id: SportIds.nba,
    displayName: 'NBA',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    // Matches the backend's own route prefix (/ncaambb/...) exactly, same
    // reasoning as ncaafb's id above.
    id: SportIds.ncaambb,
    displayName: 'NCAA MBB',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: true,
  ),
  SportConfig(
    id: SportIds.pga,
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
    id: SportIds.f1,
    displayName: 'Formula 1',
    eventShape: EventShape.field,
    accentColor: AppColors.violet,
    active: true,
    // Driver + constructor championship season simulation (aws-lambdas/
    // f1/predict/season_projection.py) -- a points-standings shape like
    // PGA's own, but with a real second (constructor) standings table
    // PGA has no analog for, hence its own F1SeasonPage rather than
    // usesFedexCupSeasonPage's PgaSeasonPage.
    hasSeasonProjection: true,
  ),
];

SportConfig sportById(String id) => kSports.firstWhere((s) => s.id == id);
