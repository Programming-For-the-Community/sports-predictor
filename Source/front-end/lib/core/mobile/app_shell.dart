import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Where this build is running. The Android app is identified by the
/// ANDROID_APP compile-time define its CI build passes, not by
/// defaultTargetPlatform -- `flutter test` also reports android on the VM.
enum AppShell { web, androidBrowser, androidApp }

const _androidAppBuild = bool.fromEnvironment('ANDROID_APP');

AppShell detectAppShell() {
  if (kIsWeb) return defaultTargetPlatform == TargetPlatform.android ? AppShell.androidBrowser : AppShell.web;
  return _androidAppBuild ? AppShell.androidApp : AppShell.web;
}

final appShellProvider = Provider<AppShell>((ref) => detectAppShell());
