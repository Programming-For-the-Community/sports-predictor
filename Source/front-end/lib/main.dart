import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';
import 'core/mobile/app_shell.dart';
import 'core/mobile/background_checks.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (detectAppShell() == AppShell.androidApp) {
    try {
      await scheduleBackgroundChecks();
    } catch (error) {
      debugPrint('[main] scheduling background checks failed: $error');
    }
  }
  runApp(const ProviderScope(child: App()));
}
