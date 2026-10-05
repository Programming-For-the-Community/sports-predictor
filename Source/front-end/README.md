# sports-predictor front-end

Flutter client for the sports-predictor platform: the website and the Android
app are built from this one codebase. See `design/FRONTEND_STYLE.md` for the
visual language and `design/ARCHITECTURE.md` for how this fits into the overall
system.

## Local development

Website:

```
flutter run -d chrome --dart-define-from-file=config/dev.json
```

Or use the "Flutter: Run web (dev)" launch configuration in `.vscode/launch.json`.

Android app (a connected device or emulator):

```
flutter run --dart-define-from-file=config/dev.json --dart-define=ANDROID_APP=true
```

`ANDROID_APP=true` is what turns on the app-only behavior (`lib/core/mobile/app_shell.dart`):
the rolling 30-day sign-in, the hourly background job (weekly model reports,
update notices, widget data), in-app updates and the home-screen widgets.
Without it the build behaves like the website. Building, signing and releasing
the APK are covered in [`android/README.md`](android/README.md).

## Tests

```
flutter analyze
flutter test
```

Tests run on the Dart VM, where Flutter reports the platform as Android, so
app-only code is gated on `appShellProvider` rather than the platform; widget
tests override that provider to exercise the website, Android-browser and
Android-app variants.

## Release notes

When a change affects the Android app, update [`RELEASE_NOTES.txt`](RELEASE_NOTES.txt)
in the same change. It's shown to users in the update notification and on the
App updates page: one or two short sentences about features and fixes, no
technical detail.
