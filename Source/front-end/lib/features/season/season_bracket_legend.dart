import 'package:flutter/material.dart';

import '../../core/models/upset.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/pick_labels.dart';

/// What a simulated bracket's legend explains: whether its slots are series,
/// and how many runs it drew from.
typedef BracketSimulationLegend = ({bool series, int simulations});

/// The key above a bracket: the dashed skip line when the bracket has one,
/// and for a simulated bracket the chips, the chance figure and (for a
/// series sport) the series wins.
class BracketLegend extends StatelessWidget {
  const BracketLegend({super.key, this.showSkip = false, this.simulation});

  static const skipText = 'Winner advances directly to a later round, skipping the Elimination Game';
  static const watchText = 'The favourite won, but the underdog had 40% or better';

  final bool showSkip;
  final BracketSimulationLegend? simulation;

  bool get isEmpty => !showSkip && simulation == null;

  static String upsetText({required bool series}) => 'The underdog won this simulated ${series ? 'series' : 'game'}';

  static String runText(int simulations) =>
      'one simulated run of ${formatSimulationCount(simulations)}, chosen to have about as many upsets as the model expects';

  @override
  Widget build(BuildContext context) {
    final simulation = this.simulation;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (showSkip)
          _entry(const CustomPaint(size: Size(28, 12), painter: _DashedLegendSwatchPainter(color: AppColors.violet)), skipText),
        if (simulation != null) ...[
          _entry(const UpsetChip(flag: UpsetFlag.upset, dense: true), upsetText(series: simulation.series)),
          _entry(const UpsetChip(flag: UpsetFlag.watch, dense: true), watchText),
          if (simulation.series) ...[
            _entry(Text('61%', style: AppTextStyles.microLabel(color: AppColors.inkMid)), 'Chance to win that game or series'),
            _entry(
              Text('4–3', style: AppTextStyles.metricValue()),
              'Series wins in ${runText(simulation.simulations)}',
            ),
          ] else
            _entry(
              Text('61%', style: AppTextStyles.microLabel(color: AppColors.inkMid)),
              'Chance to win that game · picks come from ${runText(simulation.simulations)}',
            ),
        ],
      ],
    );
  }

  Widget _entry(Widget swatch, String text) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(
          children: [
            swatch,
            const SizedBox(width: 8),
            Flexible(child: Text(text, style: AppTextStyles.microLabel(color: AppColors.inkSub))),
          ],
        ),
      );
}

/// 2000 as "2,000".
String formatSimulationCount(int value) => value.toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');

/// Small dashed-line swatch for the skip-connection legend, matching the
/// bracket's own dashed connectors.
class _DashedLegendSwatchPainter extends CustomPainter {
  const _DashedLegendSwatchPainter({required this.color});
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke;
    const dashLength = 5.0;
    const gapLength = 4.0;
    final y = size.height / 2;
    var x = 0.0;
    while (x < size.width) {
      final segmentEnd = (x + dashLength).clamp(0.0, size.width);
      canvas.drawLine(Offset(x, y), Offset(segmentEnd, y), paint);
      x += dashLength + gapLength;
    }
  }

  @override
  bool shouldRepaint(covariant _DashedLegendSwatchPainter oldDelegate) => oldDelegate.color != color;
}
