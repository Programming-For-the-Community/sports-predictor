import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/prediction_freshness_badge.dart';

void main() {
  group('PredictionFreshnessBadge', () {
    Widget wrap({
      required bool stale,
      int? retryAfterSeconds,
      bool compact = false,
      required void Function(WidgetRef, String, String) onInvalidate,
    }) {
      return MaterialApp(
        home: Scaffold(
          body: PredictionFreshnessBadge(
            sport: 'nfl',
            eventId: '1',
            stale: stale,
            retryAfterSeconds: retryAfterSeconds,
            compact: compact,
            invalidatePrediction: onInvalidate,
          ),
        ),
      );
    }

    testWidgets('renders nothing and schedules nothing when not stale', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(stale: false, retryAfterSeconds: 1, onInvalidate: (_, __, ___) => calls++));

      expect(find.text('UPDATING'), findsNothing);
      expect(find.text('Updating…'), findsNothing);

      await tester.pump(const Duration(seconds: 5));
      expect(calls, 0);
    });

    testWidgets('non-compact shows the full UPDATING pill with a spinner', (tester) async {
      await tester.pumpWidget(wrap(stale: true, retryAfterSeconds: 5, onInvalidate: (_, __, ___) {}));

      expect(find.text('UPDATING'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsOneWidget);

      await tester.pump(const Duration(seconds: 5)); // let the pending timer resolve before the test ends
    });

    testWidgets('compact shows a short inline caption instead', (tester) async {
      await tester.pumpWidget(wrap(stale: true, compact: true, retryAfterSeconds: 5, onInvalidate: (_, __, ___) {}));

      expect(find.text('Updating…'), findsOneWidget);
      expect(find.text('UPDATING'), findsNothing);

      await tester.pump(const Duration(seconds: 5));
    });

    testWidgets('retries with the sport/eventId once retryAfterSeconds elapses', (tester) async {
      var calls = 0;
      String? capturedSport;
      String? capturedEventId;
      await tester.pumpWidget(wrap(
        stale: true,
        retryAfterSeconds: 3,
        onInvalidate: (ref, sport, eventId) {
          calls++;
          capturedSport = sport;
          capturedEventId = eventId;
        },
      ));

      await tester.pump(const Duration(seconds: 2));
      expect(calls, 0);

      await tester.pump(const Duration(seconds: 2));
      expect(calls, 1);
      expect(capturedSport, 'nfl');
      expect(capturedEventId, '1');
    });

    testWidgets('defaults the retry delay to 5 seconds when the server omits it', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(stale: true, retryAfterSeconds: null, onInvalidate: (_, __, ___) => calls++));

      await tester.pump(const Duration(milliseconds: 4999));
      expect(calls, 0);

      await tester.pump(const Duration(milliseconds: 2));
      expect(calls, 1);
    });

    testWidgets('going from stale to fresh on rebuild cancels the pending retry', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(stale: true, retryAfterSeconds: 3, onInvalidate: (_, __, ___) => calls++));

      await tester.pumpWidget(wrap(stale: false, retryAfterSeconds: 3, onInvalidate: (_, __, ___) => calls++));
      await tester.pump(const Duration(seconds: 5));

      expect(calls, 0);
    });

    testWidgets('still stale on rebuild reschedules the retry from scratch', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(stale: true, retryAfterSeconds: 3, onInvalidate: (_, __, ___) => calls++));

      await tester.pump(const Duration(seconds: 2)); // below the 3s window -- not fired
      expect(calls, 0);

      await tester.pumpWidget(wrap(stale: true, retryAfterSeconds: 3, onInvalidate: (_, __, ___) => calls++));

      await tester.pump(const Duration(seconds: 2));
      // 4s since original mount, but only 2s since the reschedule -- the
      // original (unreset) timer would already have fired by now.
      expect(calls, 0);

      await tester.pump(const Duration(seconds: 2));
      expect(calls, 1);
    });

    testWidgets('disposing the widget cancels the pending retry', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(stale: true, retryAfterSeconds: 2, onInvalidate: (_, __, ___) => calls++));

      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: SizedBox())));
      await tester.pump(const Duration(seconds: 5));

      expect(calls, 0);
    });
  });
}
