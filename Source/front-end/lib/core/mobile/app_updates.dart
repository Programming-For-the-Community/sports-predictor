import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:ota_update/ota_update.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app_release.dart';
import 'app_shell.dart';

class InstalledVersion {
  const InstalledVersion({required this.versionCode, required this.versionName});

  final int versionCode;
  final String versionName;

  String get label => 'v$versionName ($versionCode)';
}

final appReleaseClientProvider = Provider<AppReleaseClient>((ref) => AppReleaseClient());

final installedVersionProvider = FutureProvider<InstalledVersion>((ref) async {
  final info = await PackageInfo.fromPlatform();
  return InstalledVersion(versionCode: int.parse(info.buildNumber), versionName: info.version);
});

/// The published APK's metadata -- read on web too, for the download prompt.
final latestReleaseProvider = FutureProvider<AppRelease?>((ref) => ref.watch(appReleaseClientProvider).fetchLatest());

/// The published release when it's newer than this install; always null
/// outside the Android app.
final pendingUpdateProvider = FutureProvider<AppRelease?>((ref) async {
  if (ref.watch(appShellProvider) != AppShell.androidApp) return null;
  final latest = await ref.watch(latestReleaseProvider.future);
  if (latest == null) return null;
  final installed = await ref.watch(installedVersionProvider.future);
  return latest.versionCode > installed.versionCode ? latest : null;
});

const _updateBannerSnoozedUntilKey = 'update_banner_snoozed_until';
const updateBannerSnooze = Duration(hours: 24);

/// "Later" on the Home update banner hides it for updateBannerSnooze; the
/// Settings dot and the App updates screen stay until the update is installed.
class UpdateBannerSnooze {
  UpdateBannerSnooze({SharedPreferences? prefs}) : _prefs = prefs;

  SharedPreferences? _prefs;

  Future<SharedPreferences> get _prefsInstance async => _prefs ??= await SharedPreferences.getInstance();

  Future<bool> isSnoozed() async {
    final raw = (await _prefsInstance).getString(_updateBannerSnoozedUntilKey);
    return raw != null && DateTime.now().isBefore(DateTime.parse(raw));
  }

  Future<void> snooze() async {
    await (await _prefsInstance).setString(_updateBannerSnoozedUntilKey, DateTime.now().add(updateBannerSnooze).toIso8601String());
  }
}

final updateBannerSnoozeProvider = Provider<UpdateBannerSnooze>((ref) => UpdateBannerSnooze());

final updateBannerSnoozedProvider = FutureProvider<bool>((ref) => ref.watch(updateBannerSnoozeProvider).isSnoozed());

/// Downloads the APK, checks it against the release's sha256, then hands it
/// to Android's installer, which always asks the user to confirm.
class AppUpdateInstaller {
  Stream<OtaEvent> install(AppRelease release) => OtaUpdate().execute(
        appReleaseUrl,
        headers: const {'User-Agent': androidAppUserAgent},
        destinationFilename: 'sports-predictor-${release.versionCode}.apk',
        sha256checksum: release.sha256,
      );
}

final appUpdateInstallerProvider = Provider<AppUpdateInstaller>((ref) => AppUpdateInstaller());
