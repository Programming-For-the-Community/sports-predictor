import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/features/assistant/assistant_client.dart';
import 'package:front_end/features/assistant/assistant_models.dart';
import 'package:front_end/features/assistant/assistant_panel.dart';

import '../../support/mobile_viewport.dart';

class _StatusClient implements AssistantClient {
  _StatusClient(this.status);
  final AssistantStatus status;

  @override
  Stream<AssistantEvent> send(AssistantMode mode, String sessionId, Map<String, Object?> body) => Stream.value(status);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Widget _panel({required bool admin, AssistantStatus? status}) => ProviderScope(
      overrides: [
        isAdminProvider.overrideWithValue(admin),
        assistantClientProvider.overrideWithValue(_StatusClient(
          status ?? const AssistantStatus(questionsLeft: 42, dailyLimit: 50, favorites: {'nfl': ['DET'], 'nba': ['DEN']}),
        )),
      ],
      child: const MaterialApp(
        home: Scaffold(
          body: AssistantPanel(context: AssistantContext(sport: 'nfl', eventId: '401', eventLabel: 'DET @ GB')),
        ),
      ),
    );

void main() {
  for (final width in [320.0, ...mobileViewportWidths]) {
    testWidgets('renders with no overflow at ${width}px wide, admin and not', (tester) async {
      await pumpAtWidth(tester, width, _panel(admin: false));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      await pumpAtWidth(tester, width, _panel(admin: true));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('only admins see the Infrastructure switch', (tester) async {
    await tester.pumpWidget(_panel(admin: false));
    await tester.pumpAndSettle();
    expect(find.text('Infrastructure'), findsNothing);
    expect(find.text('Ask about predictions'), findsOneWidget);
    expect(find.text('ABOUT  DET @ GB'), findsOneWidget);
    expect(find.text('42 OF 50 QUESTIONS LEFT TODAY'), findsOneWidget);
    expect(find.text('NFL · DET'), findsOneWidget);

    await tester.pumpWidget(_panel(admin: true));
    await tester.pumpAndSettle();
    expect(find.text('Infrastructure'), findsOneWidget);
  });

  testWidgets('switching an admin to Infrastructure hides the favourites but keeps the page context', (tester) async {
    await tester.pumpWidget(_panel(admin: true));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Infrastructure'));
    await tester.pumpAndSettle();

    expect(find.text('NFL · DET'), findsNothing);
    // The admin agent answers predictions questions too, so it gets the page's game.
    expect(find.text('ABOUT  DET @ GB'), findsOneWidget);
    expect(find.text('Ask about cost or traffic'), findsOneWidget);
  });
}
