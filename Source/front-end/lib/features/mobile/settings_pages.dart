import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:ota_update/ota_update.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/mobile/app_release.dart';
import '../../core/mobile/app_updates.dart';
import '../../core/mobile/model_report.dart';
import '../../core/mobile/notification_settings.dart';
import '../../core/routing/app_routes.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/page_glow.dart';
import 'mobile_panel.dart';

const _weekdayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// "Nov 3"
String shortDate(DateTime date) => '${_monthNames[date.month - 1]} ${date.day}';

class _SettingsScaffold extends StatelessWidget {
  const _SettingsScaffold({required this.title, required this.backLabel, required this.backRoute, required this.children});

  final String title;
  final String backLabel;
  final String backRoute;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: Stack(
        children: [
          const PageGlow(),
          SafeArea(
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton(
                    onPressed: () => context.go(backRoute),
                    child: Text('‹ $backLabel', style: AppTextStyles.body(color: AppColors.inkSub)),
                  ),
                ),
                const SizedBox(height: 8),
                Text(title, style: AppTextStyles.pageH1()),
                const SizedBox(height: 20),
                ...children,
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8, top: 4),
        child: Text(text.toUpperCase(), style: AppTextStyles.microLabel()),
      );
}

class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingUpdateProvider).value;
    return _SettingsScaffold(
      title: 'Settings',
      backLabel: 'Home',
      backRoute: AppRoutes.home,
      children: [
        MobilePanel(
          padding: EdgeInsets.zero,
          child: Column(
            children: [
              ListTile(
                title: Text('Notifications', style: AppTextStyles.body(color: AppColors.ink)),
                trailing: const Icon(Icons.chevron_right, color: AppColors.inkSub),
                onTap: () => context.go(AppRoutes.notificationSettings),
              ),
              const Divider(height: 1, color: AppColors.border),
              ListTile(
                title: Text('App updates', style: AppTextStyles.body(color: AppColors.ink)),
                subtitle: pending == null ? null : Text('v${pending.versionName} is ready', style: AppTextStyles.body(color: AppColors.warn)),
                trailing: Badge(
                  isLabelVisible: pending != null,
                  backgroundColor: AppColors.warn,
                  smallSize: 8,
                  child: const Icon(Icons.chevron_right, color: AppColors.inkSub),
                ),
                onTap: () => context.go(AppRoutes.appUpdates),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

final _sessionExpiresAtProvider = FutureProvider<DateTime?>((ref) {
  ref.watch(authRepositoryProvider);
  return ref.read(authRepositoryProvider.notifier).sessionExpiresAt();
});

class NotificationSettingsPage extends ConsumerWidget {
  const NotificationSettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(notificationSettingsProvider);
    final controller = ref.read(notificationSettingsProvider.notifier);
    final expiresAt = ref.watch(_sessionExpiresAtProvider).value;

    return _SettingsScaffold(
      title: 'Notifications',
      backLabel: 'Settings',
      backRoute: AppRoutes.settings,
      children: [
        if (settings == null)
          const Center(child: CircularProgressIndicator())
        else ...[
          const _SectionLabel('Weekly model report'),
          MobilePanel(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (final sport in reportSports)
                  SwitchListTile(
                    value: settings.reportsFor(sport.id),
                    onChanged: (enabled) => controller.setReports(sport.id, enabled),
                    title: Text.rich(TextSpan(children: [
                      TextSpan(text: sport.displayName, style: AppTextStyles.body(color: AppColors.ink)),
                      TextSpan(text: ' · ${_weekdayNames[reportWeekday[sport.id]! - 1]}', style: AppTextStyles.body(color: AppColors.inkSub)),
                    ])),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          const _SectionLabel('App'),
          MobilePanel(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                SwitchListTile(
                  value: settings.appUpdates,
                  onChanged: controller.setAppUpdates,
                  title: Text('New version available', style: AppTextStyles.body(color: AppColors.ink)),
                ),
                const Divider(height: 1, color: AppColors.border),
                ListTile(
                  title: Text.rich(TextSpan(children: [
                    TextSpan(text: 'Account', style: AppTextStyles.body(color: AppColors.ink)),
                    if (expiresAt != null)
                      TextSpan(text: ' · signed in until ${shortDate(expiresAt)}', style: AppTextStyles.body(color: AppColors.inkSub)),
                  ])),
                  trailing: TextButton(
                    onPressed: () => ref.read(authRepositoryProvider.notifier).logout(),
                    child: Text('Sign out', style: AppTextStyles.body(color: AppColors.inkSub)),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Text(
            'Reports arrive around 10 AM Eastern on each sport\'s day. Android may hold them back an hour or two to save battery.',
            style: AppTextStyles.body(color: AppColors.inkMute),
          ),
        ],
      ],
    );
  }
}

class AppUpdatesPage extends ConsumerStatefulWidget {
  const AppUpdatesPage({super.key});

  @override
  ConsumerState<AppUpdatesPage> createState() => _AppUpdatesPageState();
}

class _AppUpdatesPageState extends ConsumerState<AppUpdatesPage> {
  StreamSubscription<OtaEvent>? _install;
  OtaEvent? _lastEvent;
  DateTime _checkedAt = DateTime.now();

  @override
  void dispose() {
    _install?.cancel();
    super.dispose();
  }

  void _checkAgain() {
    ref.invalidate(latestReleaseProvider);
    setState(() => _checkedAt = DateTime.now());
  }

  void _startInstall(AppRelease release) {
    _install?.cancel();
    setState(() => _lastEvent = OtaEvent(OtaStatus.DOWNLOADING, '0'));
    _install = ref.read(appUpdateInstallerProvider).install(release).listen(
          (event) => setState(() => _lastEvent = event),
          onError: (Object error) => setState(() => _lastEvent = OtaEvent(OtaStatus.INTERNAL_ERROR, '$error')),
        );
  }

  @override
  Widget build(BuildContext context) {
    final installed = ref.watch(installedVersionProvider).value;
    final latest = ref.watch(latestReleaseProvider);
    final pending = ref.watch(pendingUpdateProvider).value;
    final downloading = _lastEvent?.status == OtaStatus.DOWNLOADING;

    return _SettingsScaffold(
      title: 'App updates',
      backLabel: 'Settings',
      backRoute: AppRoutes.settings,
      children: [
        MobilePanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _VersionRow(label: 'Installed', value: installed?.label ?? '…'),
              const SizedBox(height: 6),
              _VersionRow(
                label: 'Latest',
                value: latest.isLoading ? 'Checking…' : (latest.value?.label ?? 'Unavailable'),
                highlight: pending != null,
              ),
              if (latest.value?.notes != null) ...[
                const SizedBox(height: 12),
                const Divider(height: 1, color: AppColors.border),
                const SizedBox(height: 12),
                Text('What\'s new', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
                const SizedBox(height: 4),
                Text(latest.value!.notes!, style: AppTextStyles.body()),
              ],
            ],
          ),
        ),
        const SizedBox(height: 12),
        if (_lastEvent != null) ...[
          _InstallStatus(event: _lastEvent!, sizeBytes: pending?.sizeBytes),
          const SizedBox(height: 12),
        ],
        if (pending != null)
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: downloading ? null : () => _startInstall(pending),
              child: Text(downloading ? 'Downloading…' : 'Download & install'),
            ),
          )
        else if (latest.value != null)
          Text('You\'re on the latest version.', style: AppTextStyles.body(color: AppColors.inkSub)),
        const SizedBox(height: 8),
        Center(
          child: TextButton(
            onPressed: _checkAgain,
            child: Text(
              'Checked ${TimeOfDay.fromDateTime(_checkedAt).format(context)} · Check again',
              style: AppTextStyles.microLabel(color: AppColors.inkSub),
            ),
          ),
        ),
      ],
    );
  }
}

class _VersionRow extends StatelessWidget {
  const _VersionRow({required this.label, required this.value, this.highlight = false});

  final String label;
  final String value;
  final bool highlight;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Expanded(child: Text(label, style: AppTextStyles.body(color: AppColors.inkSub))),
          Text(value, style: AppTextStyles.microLabel(color: highlight ? AppColors.cyan : AppColors.ink)),
        ],
      );
}

class _InstallStatus extends StatelessWidget {
  const _InstallStatus({required this.event, this.sizeBytes});

  final OtaEvent event;
  final int? sizeBytes;

  @override
  Widget build(BuildContext context) {
    final message = installStatusMessage(event, sizeBytes);
    final percent = event.status == OtaStatus.DOWNLOADING ? (double.tryParse(event.value ?? '') ?? 0) / 100 : null;
    final isError = event.status.index >= OtaStatus.ALREADY_RUNNING_ERROR.index;
    return MobilePanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(message, style: AppTextStyles.body(color: isError ? AppColors.neg : AppColors.ink)),
          if (percent != null) ...[
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: LinearProgressIndicator(value: percent, minHeight: 6, backgroundColor: AppColors.surface, color: AppColors.cyan),
            ),
          ],
        ],
      ),
    );
  }
}

String installStatusMessage(OtaEvent event, int? sizeBytes) => switch (event.status) {
      OtaStatus.DOWNLOADING => sizeBytes == null
          ? 'Downloading… ${event.value ?? 0}%'
          : 'Downloading… ${formatMegabytes((sizeBytes * (double.tryParse(event.value ?? '') ?? 0) / 100).round())} / ${formatMegabytes(sizeBytes)}',
      OtaStatus.INSTALLING => 'Opening Android\'s installer. Confirm the install there.',
      OtaStatus.INSTALLATION_DONE => 'Installed. Reopen the app if it doesn\'t restart.',
      OtaStatus.PERMISSION_NOT_GRANTED_ERROR =>
        'Allow this app to install updates: Android Settings › Apps › Sports Predictor › Install unknown apps. Then tap Download & install again.',
      OtaStatus.CHECKSUM_ERROR => 'A newer build was published during the download. Tap Check again, then install.',
      OtaStatus.DOWNLOAD_ERROR => 'The download failed. Check your connection and try again.',
      OtaStatus.CANCELED => 'Install canceled.',
      _ => 'The update couldn\'t be installed (${event.value ?? event.status.name}). Try again.',
    };
