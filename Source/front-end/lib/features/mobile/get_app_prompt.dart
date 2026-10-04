import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/mobile/app_release.dart';
import '../../core/mobile/app_updates.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

/// The header pill on the website in an Android browser (home_page.dart):
/// opens the install sheet. Hidden until a published APK is found.
class GetAppButton extends ConsumerWidget {
  const GetAppButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final release = ref.watch(latestReleaseProvider).value;
    if (release == null) return const SizedBox.shrink();
    return OutlinedButton.icon(
      onPressed: () => showInstallSheet(context, release),
      icon: const Icon(Icons.download, size: 16, color: AppColors.cyan),
      label: Text('Get app', style: AppTextStyles.body(color: AppColors.cyan).copyWith(fontWeight: FontWeight.w600)),
      style: OutlinedButton.styleFrom(
        side: BorderSide(color: AppColors.cyan.withValues(alpha: 0.45)),
        shape: const StadiumBorder(),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        visualDensity: VisualDensity.compact,
      ),
    );
  }
}

Future<void> showInstallSheet(BuildContext context, AppRelease release) => showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.bgDeep,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (context) => _InstallSheet(release: release),
    );

class _InstallSheet extends ConsumerWidget {
  const _InstallSheet({required this.release});

  final AppRelease release;

  static const _steps = [
    'Tap Download. Chrome may warn that this type of file can be harmful. Choose "Download anyway".',
    'Open the file. The first time, Android asks you to allow your browser to install apps. Turn on "Allow from this source".',
    'Tap Install, then sign in once. You stay signed in for 30 days.',
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final size = release.sizeBytes == null ? '' : ' · ${formatMegabytes(release.sizeBytes!)}';
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text('Install on Android', style: AppTextStyles.sectionTitle())),
                Text('v${release.versionName}', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
              ],
            ),
            const SizedBox(height: 16),
            for (var i = 0; i < _steps.length; i++) ...[
              _Step(number: i + 1, text: _steps[i]),
              const SizedBox(height: 10),
            ],
            const SizedBox(height: 6),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: () {
                  final auth = ref.read(authRepositoryProvider);
                  final username = auth is AuthAuthenticated ? auth.tokens.username : null;
                  launchUrl(Uri.parse(appDownloadUrl(source: DownloadSource.web, username: username)), webOnlyWindowName: '_self');
                },
                child: Text('Download APK$size'),
              ),
            ),
            const SizedBox(height: 10),
            Text(
              'SHA-256 ${_shortHash(release.sha256)} · Later updates install from inside the app',
              style: AppTextStyles.microLabel(),
            ),
          ],
        ),
      ),
    );
  }
}

String _shortHash(String sha256) => sha256.length <= 12 ? sha256 : '${sha256.substring(0, 4)}…${sha256.substring(sha256.length - 4)}';

class _Step extends StatelessWidget {
  const _Step({required this.number, required this.text});

  final int number;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 22,
          height: 22,
          alignment: Alignment.center,
          decoration: BoxDecoration(shape: BoxShape.circle, color: AppColors.cyan.withValues(alpha: 0.15)),
          child: Text('$number', style: AppTextStyles.microLabel(color: AppColors.cyan)),
        ),
        const SizedBox(width: 10),
        Expanded(child: Text(text, style: AppTextStyles.body())),
      ],
    );
  }
}
