import 'package:flutter/material.dart';

import '../../static/model_display.dart';
import '../models/model_performance.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'model_card_frame.dart';
import 'model_performance_card_view.dart';
import 'model_performance_format.dart';

// Widths of the two figure columns at the default text size, shared by the
// column header and every row so the figures line up down the list.
const _seasonWidth = 58.0;
const _lastWidth = 84.0;
const _chevronSize = 20.0;
const _cellGap = 8.0;
// Below this much room for the model's name (scaled by the system text size)
// a row puts its figures on a second line instead of squeezing the name.
const _minNameWidth = 110.0;
const _rowPadding = EdgeInsets.symmetric(horizontal: 12, vertical: 10);
const _openDuration = Duration(milliseconds: 180);

/// The Performance tab on a phone: one row per model -- its name, season
/// figure and last-period figure with how that compares -- that opens into
/// the full card. Opening one closes whichever was open.
class ModelPerformanceAccordion extends StatefulWidget {
  const ModelPerformanceAccordion({
    super.key,
    required this.records,
    required this.isWeekly,
    this.seasonLabel = 'THIS SEASON',
    this.sport = '',
  });

  final List<ModelPerformanceRecord> records;
  final bool isWeekly;
  final String seasonLabel;
  final String sport;

  @override
  State<ModelPerformanceAccordion> createState() => _ModelPerformanceAccordionState();
}

class _ModelPerformanceAccordionState extends State<ModelPerformanceAccordion> {
  String? _open;

  /// Opens `modelName`, closing any other, or closes it if it was open.
  void _toggle(String modelName) => setState(() => _open = _open == modelName ? null : modelName);

  @override
  Widget build(BuildContext context) {
    final seasonHeading = widget.seasonLabel.replaceFirst('THIS ', '');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _ColumnHeader(seasonHeading: seasonHeading, lastHeading: widget.isWeekly ? 'LAST WEEK' : 'LAST EVENT'),
        for (final record in widget.records) ...[
          const SizedBox(height: 8),
          _AccordionCard(
            record: record,
            isWeekly: widget.isWeekly,
            seasonLabel: widget.seasonLabel,
            sport: widget.sport,
            open: _open == record.modelName,
            onToggle: () => _toggle(record.modelName),
          ),
        ],
      ],
    );
  }
}

/// Lays out a row's name and its two figure cells: on one line when there is
/// room, else the figures right-aligned on a line of their own.
class _RowLayout extends StatelessWidget {
  const _RowLayout({required this.name, required this.season, required this.last, required this.trailing});

  final Widget name;
  final Widget season;
  final Widget last;
  final Widget trailing;

  @override
  Widget build(BuildContext context) {
    final scaler = MediaQuery.textScalerOf(context);
    final seasonWidth = scaler.scale(_seasonWidth);
    final lastWidth = scaler.scale(_lastWidth);
    // Flexible so the stacked line can still shrink a cell (its text then
    // wraps) on the narrowest phones at a large text size.
    final figures = [
      Flexible(child: SizedBox(width: seasonWidth, child: Align(alignment: Alignment.centerRight, child: season))),
      const SizedBox(width: _cellGap),
      Flexible(child: SizedBox(width: lastWidth, child: Align(alignment: Alignment.centerRight, child: last))),
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        final fixed = seasonWidth + lastWidth + _chevronSize + 3 * _cellGap;
        if (constraints.maxWidth - fixed >= scaler.scale(_minNameWidth)) {
          return Row(children: [Expanded(child: name), const SizedBox(width: _cellGap), ...figures, const SizedBox(width: _cellGap), trailing]);
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [Expanded(child: name), const SizedBox(width: _cellGap), trailing]),
            const SizedBox(height: 6),
            Row(mainAxisAlignment: MainAxisAlignment.end, children: [...figures, const SizedBox(width: _cellGap + _chevronSize)]),
          ],
        );
      },
    );
  }
}

class _ColumnHeader extends StatelessWidget {
  const _ColumnHeader({required this.seasonHeading, required this.lastHeading});

  final String seasonHeading;
  final String lastHeading;

  @override
  Widget build(BuildContext context) {
    final style = AppTextStyles.microLabel(color: AppColors.inkSub);
    return ExcludeSemantics(
      child: Padding(
        // Lines up with the rows' content inside their 1px border.
        padding: EdgeInsets.symmetric(horizontal: _rowPadding.left + 1),
        child: _RowLayout(
          name: Text('MODEL', style: style),
          season: Text(seasonHeading, style: style, textAlign: TextAlign.right),
          last: Text(lastHeading, style: style, textAlign: TextAlign.right),
          trailing: const SizedBox(width: _chevronSize),
        ),
      ),
    );
  }
}

class _AccordionCard extends StatelessWidget {
  const _AccordionCard({
    required this.record,
    required this.isWeekly,
    required this.seasonLabel,
    required this.sport,
    required this.open,
    required this.onToggle,
  });

  final ModelPerformanceRecord record;
  final bool isWeekly;
  final String seasonLabel;
  final String sport;
  final bool open;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final display = modelDisplay(record.modelName);
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: open ? AppColors.cyan.withValues(alpha: 0.35) : AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Header(record: record, display: display, open: open, onToggle: onToggle),
          AnimatedSize(
            duration: _openDuration,
            curve: Curves.easeOut,
            alignment: Alignment.topCenter,
            // Once opened -- and any card above has folded away -- bring this
            // row to the top so the page never leaves it scrolled off-screen.
            onEnd: () {
              if (open) Scrollable.ensureVisible(context, duration: _openDuration, curve: Curves.easeOut);
            },
            child: open ? _Body(record: record, isWeekly: isWeekly, seasonLabel: seasonLabel, sport: sport) : const SizedBox(width: double.infinity),
          ),
        ],
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.record, required this.display, required this.open, required this.onToggle});

  final ModelPerformanceRecord record;
  final ModelDisplay display;
  final bool open;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final name = modelDisplayName(record.modelName);
    final season = _figure(record.season.value);
    final last = _figure(record.lastPeriod?.value);
    final delta = compactDelta(record, display);
    return Semantics(
      button: true,
      expanded: open,
      label: '$name. Season ${season.spoken}. Last ${last.spoken}.',
      excludeSemantics: true,
      child: Material(
        type: MaterialType.transparency,
        child: InkWell(
          onTap: onToggle,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 48),
            child: Padding(
              padding: _rowPadding,
              child: _RowLayout(
                name: Text(name, style: AppTextStyles.body(color: AppColors.ink).copyWith(fontSize: 14, fontWeight: FontWeight.w600)),
                season: Text.rich(season.span, textAlign: TextAlign.right),
                last: Text.rich(
                  TextSpan(children: [
                    last.span,
                    if (delta != null)
                      TextSpan(
                        text: ' ${deltaGlyph(delta.tone).trim()}${delta.text}',
                        style: AppTextStyles.metricValue(color: deltaToneColor(delta.tone)).copyWith(fontSize: 11),
                      ),
                  ]),
                  textAlign: TextAlign.right,
                ),
                trailing: AnimatedRotation(
                  turns: open ? 0.5 : 0,
                  duration: _openDuration,
                  child: const Icon(Icons.expand_more, size: _chevronSize, color: AppColors.inkSub),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// A figure as styled text ("64.6%", "5.0 pts") plus how a screen reader says it.
  ({InlineSpan span, String spoken}) _figure(double? value) {
    final number = value == null ? '--' : headlineNumber(record, display, value);
    final unit = record.isAmount && value != null && display.valueUnit.isNotEmpty ? display.valueUnit : '';
    return (
      span: TextSpan(children: [
        TextSpan(text: number, style: AppTextStyles.metricValue(color: AppColors.ink).copyWith(fontSize: 13)),
        if (unit.isNotEmpty) TextSpan(text: ' $unit', style: AppTextStyles.body(color: AppColors.inkSub).copyWith(fontSize: 10)),
      ]),
      spoken: unit.isEmpty ? number : '$number $unit',
    );
  }
}

/// An open row: the card's badges, details and footer -- the full card
/// without its title, which the row above already shows.
class _Body extends StatelessWidget {
  const _Body({required this.record, required this.isWeekly, required this.seasonLabel, required this.sport});

  final ModelPerformanceRecord record;
  final bool isWeekly;
  final String seasonLabel;
  final String sport;

  @override
  Widget build(BuildContext context) {
    final badges = performanceBadges(record);
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 14, 12, 16),
      decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.border))),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (badges.isNotEmpty) ...[
            Wrap(spacing: 8, runSpacing: 8, children: [for (final badge in badges) ModelCardBadge(text: badge)]),
            const SizedBox(height: 14),
          ],
          ModelPerformanceDetails(record: record, isWeekly: isWeekly, seasonLabel: seasonLabel, sport: sport),
          if (ModelPerformanceFooter.shows(record)) ...[
            const SizedBox(height: 20),
            ModelPerformanceFooter(record: record, isWeekly: isWeekly),
          ],
        ],
      ),
    );
  }
}
