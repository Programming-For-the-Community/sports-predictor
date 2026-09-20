import 'dart:async';

import 'package:flutter/widgets.dart';

/// Shared Timer.periodic polling shell -- event_detail_page.dart,
/// f1_event_detail_page.dart, and field_event_detail_page.dart all
/// re-poll their own data on a fixed interval AND on app/tab resume
/// (browsers and mobile OSes throttle or pause a backgrounded tab's own
/// timers, so a plain Timer.periodic alone can silently stop firing while
/// backgrounded, with nothing catching it back up once foregrounded
/// again). Apply this mixin AFTER WidgetsBindingObserver in the state
/// class's own `with` clause -- mixin composition order means this
/// mixin's own didChangeAppLifecycleState then correctly overrides
/// WidgetsBindingObserver's no-op default.
///
/// Each state implements pollInterval and onPoll(). onResume() defaults
/// to calling onPoll(); override it (calling onPoll() first, same as the
/// default) if resume needs extra work -- event_detail_page.dart's own
/// override also refreshes its two eventsListProvider buckets after
/// onPoll(), so a stale cached event status doesn't keep this page stuck
/// on a pre-game/mid-game layout after the event has actually completed.
mixin PollingStateMixin<T extends StatefulWidget> on State<T> {
  Timer? _pollTimer;

  Duration get pollInterval;
  void onPoll();
  void onResume() => onPoll();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this as WidgetsBindingObserver);
    _pollTimer = Timer.periodic(pollInterval, (_) => onPoll());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this as WidgetsBindingObserver);
    _pollTimer?.cancel();
    super.dispose();
  }

  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) onResume();
  }
}
