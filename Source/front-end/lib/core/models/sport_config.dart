import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// Determines UI shape, not data shape -- design/DATA_SCHEMA.md's
/// `participants` array is the same structure either way. h2h shows win
/// probability + margin; field events show a finishing-position
/// distribution (not yet built -- no field-event sport has a backend yet).
enum EventShape { headToHead, field }

/// One entry per sport this app knows about, active or not. Adding a new
/// sport to the backend means flipping `active: true` here -- no new
/// widgets, no new repository code, since every sport hits the same
/// `/{sport}/events`, `/{sport}/predictions/...`, `/{sport}/models` route
/// shapes by backend design (see design/CLAUDE.md item 3).
class SportConfig {
  const SportConfig({
    required this.id,
    required this.displayName,
    required this.eventShape,
    required this.accentColor,
    required this.active,
  });

  final String id;
  final String displayName;
  final EventShape eventShape;
  final Color accentColor;
  final bool active;
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
    active: false,
  ),
  SportConfig(
    id: 'ncaa_mbb',
    displayName: 'NCAA MBB',
    eventShape: EventShape.headToHead,
    accentColor: AppColors.cyan,
    active: false,
  ),
  SportConfig(
    id: 'pga',
    displayName: 'PGA Tour',
    eventShape: EventShape.field,
    accentColor: AppColors.violet,
    active: false,
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
