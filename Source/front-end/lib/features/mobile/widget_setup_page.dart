import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_widget/home_widget.dart';

import '../../core/api/api_client.dart';
import '../../core/mobile/widget_data.dart';
import '../../core/mobile/widget_sync.dart';
import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/page_glow.dart';
import 'mobile_panel.dart';

final widgetHostProvider = Provider<WidgetHost>((ref) => const DeviceWidgetHost());

/// Finishes Android's widget-configure flow; a no-op outside it.
final finishWidgetConfigureProvider = Provider<Future<void> Function()>((ref) => HomeWidget.finishHomeWidgetConfigure);

/// Opened by Android when a widget is placed or reconfigured
/// (WidgetConfigureActivity): picks the sport this widget shows.
class WidgetSetupPage extends ConsumerStatefulWidget {
  const WidgetSetupPage({super.key, required this.widgetId, required this.kind});

  final int widgetId;
  final HomeWidgetKind kind;

  @override
  ConsumerState<WidgetSetupPage> createState() => _WidgetSetupPageState();
}

class _WidgetSetupPageState extends ConsumerState<WidgetSetupPage> {
  String? _saving;

  Future<void> _choose(SportConfig sport) async {
    setState(() => _saving = sport.id);
    final host = ref.read(widgetHostProvider);
    try {
      await host.setSport(widget.widgetId, sport.id);
      try {
        final api = ref.read(apiClientProvider);
        final data = await fetchWidgetData(widget.kind, sport, api.get, now: DateTime.now().toUtc());
        await host.save(widget.kind.dataKey(sport.id), data);
      } catch (error) {
        // The sport is already saved, so the hourly background job fills
        // the widget in later.
        debugPrint('[WidgetSetup] first load for ${sport.id} failed: $error');
      }
      // Replaces the layout's sample preview with this widget's own state,
      // even if that state is only "Open the app to load".
      await host.redraw(widget.kind);
    } catch (error) {
      debugPrint('[WidgetSetup] setting up ${sport.id} failed: $error');
    } finally {
      // Always return to the home screen -- Android keeps the widget only
      // once configuration is finished.
      await ref.read(finishWidgetConfigureProvider)();
    }
  }

  @override
  Widget build(BuildContext context) {
    final sports = kSports.where((s) => s.active && (widget.kind == HomeWidgetKind.topPicks || s.hasPerformanceTab)).toList();
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: Stack(
        children: [
          const PageGlow(),
          SafeArea(
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                Text(widget.kind.title, style: AppTextStyles.pageH1()),
                const SizedBox(height: 8),
                Text('Choose the sport this widget shows.', style: AppTextStyles.body(color: AppColors.inkSub)),
                const SizedBox(height: 20),
                MobilePanel(
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      for (var i = 0; i < sports.length; i++) ...[
                        if (i > 0) const Divider(height: 1, color: AppColors.border),
                        ListTile(
                          enabled: _saving == null,
                          title: Text(sports[i].displayName, style: AppTextStyles.body(color: AppColors.ink)),
                          trailing: _saving == sports[i].id
                              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                              : const Icon(Icons.chevron_right, color: AppColors.inkSub),
                          onTap: () => _choose(sports[i]),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
