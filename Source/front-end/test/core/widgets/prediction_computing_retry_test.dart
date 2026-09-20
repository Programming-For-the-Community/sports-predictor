import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/prediction_computing_retry.dart';

void main() {
  group('PredictionComputingRetry', () {
    Widget wrap({int retryAfterSeconds = 2, bool compact = false, required void Function(WidgetRef, String, String) onInvalidate}) {
      return MaterialApp(
        home: Scaffold(
          body: PredictionComputingRetry(
            sport: 'nfl',
            eventId: '1',
            retryAfterSeconds: retryAfterSeconds,
            compact: compact,
            invalidatePrediction: onInvalidate,
          ),
        ),
      );
    }

    testWidgets('shows a spinner and the computing message', (tester) async {
      await tester.pumpWidget(wrap(onInvalidate: (_, __, ___) {}));

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(find.text('Computing prediction...'), findsOneWidget);
    });

    testWidgets('does not retry before retryAfterSeconds has elapsed', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(retryAfterSeconds: 2, onInvalidate: (_, __, ___) => calls++));

      // Jitter only ever ADDS to the base delay, so anything short of the
      // base itself is a safe "definitely not yet" bound regardless of the
      // random jitter draw.
      await tester.pump(const Duration(milliseconds: 1999));

      expect(calls, 0);
      await tester.pump(const Duration(seconds: 3)); // let the pending timer resolve before the test ends
    });

    testWidgets('retries with the sport/eventId once retryAfterSeconds (plus jitter) elapses', (tester) async {
      var calls = 0;
      String? capturedSport;
      String? capturedEventId;
      await tester.pumpWidget(wrap(
        retryAfterSeconds: 2,
        onInvalidate: (ref, sport, eventId) {
          calls++;
          capturedSport = sport;
          capturedEventId = eventId;
        },
      ));

      // Max possible delay is base + 40% (2000 + 800ms) -- comfortably past that.
      await tester.pump(const Duration(milliseconds: 2900));

      expect(calls, 1);
      expect(capturedSport, 'nfl');
      expect(capturedEventId, '1');
    });

    testWidgets('reschedules the retry from scratch on every rebuild', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(retryAfterSeconds: 2, onInvalidate: (_, __, ___) => calls++));

      await tester.pump(const Duration(milliseconds: 1500)); // below the 2000ms minimum -- not fired
      expect(calls, 0);

      // Rebuilds the same widget in place -- didUpdateWidget cancels and
      // restarts the countdown rather than letting the original timer run.
      await tester.pumpWidget(wrap(retryAfterSeconds: 2, onInvalidate: (_, __, ___) => calls++));

      await tester.pump(const Duration(milliseconds: 1500));
      // 3000ms since the original mount, but only 1500ms since the reset --
      // if didUpdateWidget hadn't rescheduled, the original timer (max
      // 2800ms) would already have fired by now.
      expect(calls, 0);

      await tester.pump(const Duration(milliseconds: 1500)); // now past the reset countdown's max
      expect(calls, 1);
    });

    testWidgets('disposing the widget cancels the pending retry', (tester) async {
      var calls = 0;
      await tester.pumpWidget(wrap(retryAfterSeconds: 2, onInvalidate: (_, __, ___) => calls++));

      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: SizedBox())));
      await tester.pump(const Duration(seconds: 5));

      expect(calls, 0);
    });

    testWidgets('compact renders inline with no crash', (tester) async {
      await tester.pumpWidget(wrap(compact: true, onInvalidate: (_, __, ___) {}));

      expect(find.byType(Row), findsWidgets);
      expect(find.text('Computing prediction...'), findsOneWidget);
    });
  });
}
