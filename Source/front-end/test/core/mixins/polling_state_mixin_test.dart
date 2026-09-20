import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/mixins/polling_state_mixin.dart';

class _PollingWidget extends StatefulWidget {
  const _PollingWidget({required this.pollInterval, required this.onPoll, this.onResumeOverride});
  final Duration pollInterval;
  final VoidCallback onPoll;
  final VoidCallback? onResumeOverride;

  @override
  State<_PollingWidget> createState() => _PollingWidgetState();
}

class _PollingWidgetState extends State<_PollingWidget> with WidgetsBindingObserver, PollingStateMixin<_PollingWidget> {
  @override
  Duration get pollInterval => widget.pollInterval;

  @override
  void onPoll() => widget.onPoll();

  @override
  void onResume() {
    if (widget.onResumeOverride != null) {
      widget.onResumeOverride!();
    } else {
      super.onResume();
    }
  }

  @override
  Widget build(BuildContext context) => const SizedBox();
}

void main() {
  group('PollingStateMixin', () {
    testWidgets('polls once per pollInterval', (tester) async {
      var calls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(pollInterval: const Duration(seconds: 10), onPoll: () => calls++),
      ));

      await tester.pump(const Duration(seconds: 10));
      expect(calls, 1);

      await tester.pump(const Duration(seconds: 10));
      expect(calls, 2);
    });

    testWidgets('does not poll before pollInterval elapses', (tester) async {
      var calls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(pollInterval: const Duration(seconds: 10), onPoll: () => calls++),
      ));

      await tester.pump(const Duration(seconds: 9));
      expect(calls, 0);
    });

    testWidgets('stops polling once disposed', (tester) async {
      var calls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(pollInterval: const Duration(seconds: 10), onPoll: () => calls++),
      ));

      await tester.pumpWidget(const MaterialApp(home: SizedBox()));
      await tester.pump(const Duration(seconds: 30));

      expect(calls, 0);
    });

    testWidgets('onResume defaults to calling onPoll on app resume', (tester) async {
      var calls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(pollInterval: const Duration(minutes: 5), onPoll: () => calls++),
      ));
      expect(calls, 0);

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pump();

      expect(calls, 1);
    });

    testWidgets('a non-resumed lifecycle state does not trigger onResume', (tester) async {
      var calls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(pollInterval: const Duration(minutes: 5), onPoll: () => calls++),
      ));

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
      await tester.pump();
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      await tester.pump();

      expect(calls, 0);
    });

    testWidgets('a state overriding onResume runs its own logic instead of the default', (tester) async {
      var pollCalls = 0;
      var resumeCalls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(
          pollInterval: const Duration(minutes: 5),
          onPoll: () => pollCalls++,
          onResumeOverride: () => resumeCalls++,
        ),
      ));

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pump();

      expect(resumeCalls, 1);
      expect(pollCalls, 0); // the override didn't call onPoll() itself
    });

    testWidgets('removing the observer on dispose stops resume from firing after unmount', (tester) async {
      var calls = 0;
      await tester.pumpWidget(MaterialApp(
        home: _PollingWidget(pollInterval: const Duration(minutes: 5), onPoll: () => calls++),
      ));

      await tester.pumpWidget(const MaterialApp(home: SizedBox()));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pump();

      expect(calls, 0);
    });
  });
}
