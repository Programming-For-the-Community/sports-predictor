import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/models/field_event.dart';
import 'package:front_end/features/events/cup_match_list.dart';

import '../../support/mobile_viewport.dart';

Map<String, dynamic> _side(String teamId, String abbreviation, List<String> golfers, {Map<String, dynamic>? result}) => {
      'entity_id': teamId, 'name': abbreviation, 'abbreviation': abbreviation,
      'golfers': [for (final g in golfers) {'entity_id': g, 'name': g}],
      if (result != null) 'result': result,
    };

List<FieldEvent> _matches() => [
      FieldEvent.fromJson({
        'event_id': '401824815-match-1', 'event_type': 'match_play', 'event_date': '2026-09-24', 'status': 'completed',
        'session_name': 'Thursday Four-Balls', 'match_time': '2026-09-24T16:00Z',
        'participants': [
          _side('1', 'USA', ['Scottie Scheffler', 'Xander Schauffele'], result: {'won': true, 'margin_display': '3 & 2'}),
          _side('3', 'INTL', ['Hideki Matsuyama', 'Sungjae Im'], result: {'won': false, 'margin_display': ''}),
        ],
      }),
      FieldEvent.fromJson({
        'event_id': '401824815-match-2', 'event_type': 'match_play', 'event_date': '2026-09-24', 'status': 'completed',
        'session_name': 'Thursday Four-Balls', 'match_time': '2026-09-24T16:12Z',
        'participants': [
          _side('1', 'USA', ['Patrick Cantlay', 'Collin Morikawa'], result: {'halved': true}),
          _side('3', 'INTL', ['Cristóbal Del Solar-Hernández', 'Christiaan Bezuidenhout'], result: {'halved': true}),
        ],
      }),
      FieldEvent.fromJson({
        'event_id': '401824815-match-3', 'event_type': 'match_play', 'event_date': '2026-09-26', 'status': 'scheduled',
        'session_name': 'Saturday Morning Four-Balls', 'match_time': '2026-09-26T12:05Z',
        'participants': [
          _side('1', 'USA', ['Justin Thomas', 'Sam Burns']),
          _side('3', 'INTL', ['Adam Scott', 'Tom Kim']),
        ],
      }),
    ];

Widget _app(List<FieldEvent> matches) => ProviderScope(
      overrides: [fieldChildEventsProvider.overrideWith((ref, query) async => matches)],
      child: const MaterialApp(home: Scaffold(body: SingleChildScrollView(child: CupMatchList(sport: 'pga', cupEventId: '401824815')))),
    );

void main() {
  testWidgets('groups matches under their session headings with pairings and results', (tester) async {
    await tester.pumpWidget(_app(_matches()));
    await tester.pumpAndSettle();

    expect(find.text('Thursday Four-Balls'), findsOneWidget);
    expect(find.text('Saturday Morning Four-Balls'), findsOneWidget);
    expect(find.text('Scottie Scheffler / Xander Schauffele (USA)'), findsOneWidget);
    expect(find.text('3 & 2'), findsOneWidget);
    expect(find.text('Halved'), findsOneWidget);
  });

  testWidgets('renders nothing when the cup has no matches yet', (tester) async {
    await tester.pumpWidget(_app(const []));
    await tester.pumpAndSettle();

    expect(find.byType(InkWell), findsNothing);
  });

  testWidgets('tapping a match pushes its own detail route', (tester) async {
    final router = GoRouter(routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => const Scaffold(body: SingleChildScrollView(child: CupMatchList(sport: 'pga', cupEventId: '401824815'))),
      ),
      GoRoute(path: '/pga/events/401824815-match-3', builder: (context, state) => const Scaffold(body: Text('Match Detail'))),
    ]);
    await tester.pumpWidget(ProviderScope(
      overrides: [fieldChildEventsProvider.overrideWith((ref, query) async => _matches())],
      child: MaterialApp.router(routerConfig: router),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Adam Scott / Tom Kim (INTL)'));
    await tester.pumpAndSettle();

    expect(find.text('Match Detail'), findsOneWidget);
    expect(router.canPop(), isTrue);
  });

  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(tester, width, _app(_matches()));

      expect(tester.takeException(), isNull);
    });
  }
}
