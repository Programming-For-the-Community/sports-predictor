import 'package:flutter/material.dart';

import '../../core/models/season_projection.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../static/conference_order.dart';
import 'season_bracket_section.dart';

/// One row per conference tournament, collapsed by default -- avoids
/// rendering every conference's bracket at once. Tapping a row reveals its
/// own BracketSection underneath.
class ConferenceBracketsSection extends StatefulWidget {
  const ConferenceBracketsSection({super.key, required this.sport, required this.conferenceBrackets});

  final String sport;
  final List<ConferenceBracket> conferenceBrackets;

  @override
  State<ConferenceBracketsSection> createState() => _ConferenceBracketsSectionState();
}

class _ConferenceBracketsSectionState extends State<ConferenceBracketsSection> {
  final Set<String> _expanded = {};

  @override
  Widget build(BuildContext context) {
    final sorted = [...widget.conferenceBrackets]
      ..sort((a, b) => compareConferenceOrder(a.conference, b.conference));
    return Column(
      children: [
        for (final entry in sorted) ...[
          _ConferenceBracketRow(
            sport: widget.sport,
            conferenceBracket: entry,
            expanded: _expanded.contains(entry.conference),
            onTap: () => setState(() {
              if (!_expanded.remove(entry.conference)) _expanded.add(entry.conference);
            }),
          ),
          const SizedBox(height: 12),
        ],
      ],
    );
  }
}

class _ConferenceBracketRow extends StatelessWidget {
  const _ConferenceBracketRow({
    required this.sport,
    required this.conferenceBracket,
    required this.expanded,
    required this.onTap,
  });

  final String sport;
  final ConferenceBracket conferenceBracket;
  final bool expanded;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: AppColors.surfaceGrad),
        border: Border.all(color: AppColors.borderRaised),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(16),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      conferenceBracket.conference.toUpperCase(),
                      style: AppTextStyles.body(color: AppColors.ink),
                    ),
                  ),
                  Icon(expanded ? Icons.expand_less : Icons.expand_more, color: AppColors.inkMute),
                ],
              ),
            ),
          ),
          if (expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: BracketSection(sport: sport, bracket: conferenceBracket.bracket),
            ),
        ],
      ),
    );
  }
}
