import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/field_live_score.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/sport_config.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/sport_card.dart';

const _nfl = SportConfig(id: 'nfl', displayName: 'NFL', eventShape: EventShape.headToHead, accentColor: AppColors.cyan, active: true);
const _pga = SportConfig(id: 'pga', displayName: 'PGA Tour', eventShape: EventShape.field, accentColor: AppColors.violet, active: true);
const _f1 = SportConfig(id: 'f1', displayName: 'Formula 1', eventShape: EventShape.field, accentColor: AppColors.violet, active: false);
const _f1Active = SportConfig(id: 'f1', displayName: 'Formula 1', eventShape: EventShape.field, accentColor: AppColors.violet, active: true);

// Finds the small colored dot/glow indicator -- the first DecoratedBox with
// a BoxShape.circle in the tree (SportCard's own left-of-title dot).
Finder _dot() => find.byWidgetPredicate(
      (w) => w is Container && w.decoration is BoxDecoration && (w.decoration! as BoxDecoration).shape == BoxShape.circle,
    );

void main() {
  testWidgets('a not-yet-implemented sport shows SOON, muted, no glow', (tester) async {
    await tester.pumpWidget(
      ProviderScope(child: MaterialApp(home: Scaffold(body: SportCard(sport: _f1)))),
    );

    expect(find.text('SOON'), findsOneWidget);
    expect(find.text('LIVE'), findsNothing);
    expect(find.text('ACTIVE'), findsNothing);
    expect(find.text('Coming soon'), findsOneWidget);

    final dot = tester.widget<Container>(_dot());
    final decoration = dot.decoration! as BoxDecoration;
    expect(decoration.color, AppColors.inkMute);
    expect(decoration.boxShadow, isNull);
  });

  testWidgets('an implemented head-to-head sport with no live event shows ACTIVE, dim, no glow', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [liveScoresProvider.overrideWith((ref, sport) async => const {})],
        child: MaterialApp(home: Scaffold(body: SportCard(sport: _nfl))),
      ),
    );
    await tester.pump();

    expect(find.text('ACTIVE'), findsOneWidget);
    expect(find.text('LIVE'), findsNothing);

    final dot = tester.widget<Container>(_dot());
    final decoration = dot.decoration! as BoxDecoration;
    expect(decoration.boxShadow, isNull);
  });

  testWidgets('an implemented head-to-head sport with a live event shows LIVE, glowing', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          liveScoresProvider.overrideWith(
            (ref, sport) async => const {'401': LiveEventState(live: true, detail: 'Q3 08:14', homeScore: 14, awayScore: 7)},
          ),
        ],
        child: MaterialApp(home: Scaffold(body: SportCard(sport: _nfl))),
      ),
    );
    await tester.pump();

    expect(find.text('LIVE'), findsOneWidget);
    expect(find.text('ACTIVE'), findsNothing);

    final dot = tester.widget<Container>(_dot());
    final decoration = dot.decoration! as BoxDecoration;
    expect(decoration.color, AppColors.cyan.withValues(alpha: 1));
    expect(decoration.boxShadow, isNotNull);
  });

  testWidgets('an implemented field sport with an in-progress golfer shows LIVE, glowing', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          pgaLiveScoresProvider.overrideWith(
            (ref, sport) async => {
              '401': PgaFieldLiveState(
                const FieldLiveEventState(
                  status: 'scheduled',
                  participants: {'1': FieldParticipantLiveResult(status: 'in_progress')},
                ),
              ),
            },
          ),
        ],
        child: MaterialApp(home: Scaffold(body: SportCard(sport: _pga))),
      ),
    );
    await tester.pump();

    expect(find.text('LIVE'), findsOneWidget);
  });

  testWidgets('an implemented field sport with only a scheduled (not-yet-teed-off) entry shows ACTIVE, not LIVE', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          pgaLiveScoresProvider.overrideWith(
            (ref, sport) async => {
              '401': PgaFieldLiveState(const FieldLiveEventState(status: 'scheduled', participants: {})),
            },
          ),
        ],
        child: MaterialApp(home: Scaffold(body: SportCard(sport: _pga))),
      ),
    );
    await tester.pump();

    expect(find.text('ACTIVE'), findsOneWidget);
    expect(find.text('LIVE'), findsNothing);
  });

  testWidgets('an implemented F1 with a live ESPN session shows LIVE, glowing', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          f1LiveScoresProvider.overrideWith(
            (ref, sport) async => {'2026-5': const F1LiveEventState(eventType: 'field', state: 'in')},
          ),
        ],
        child: MaterialApp(home: Scaffold(body: SportCard(sport: _f1Active))),
      ),
    );
    await tester.pump();

    expect(find.text('LIVE'), findsOneWidget);
    expect(find.text('ACTIVE'), findsNothing);
  });

  testWidgets('an implemented F1 with no live ESPN session shows ACTIVE, not LIVE', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [f1LiveScoresProvider.overrideWith((ref, sport) async => const {})],
        child: MaterialApp(home: Scaffold(body: SportCard(sport: _f1Active))),
      ),
    );
    await tester.pump();

    expect(find.text('ACTIVE'), findsOneWidget);
    expect(find.text('LIVE'), findsNothing);
  });
}
