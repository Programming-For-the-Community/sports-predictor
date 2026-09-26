import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/data/field_events_repository.dart';
import '../../core/models/event_status.dart';
import '../../core/models/field_event.dart';
import '../../core/routing/app_routes.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/game_row.dart' show clockLabel;

/// A Ryder Cup/Presidents Cup's own matches, grouped by session in tee-off
/// order. Each row opens that match's own detail page.
class CupMatchList extends ConsumerWidget {
  const CupMatchList({super.key, required this.sport, required this.cupEventId});

  final String sport;
  final String cupEventId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ref.watch(fieldChildEventsProvider((sport: sport, eventId: cupEventId))).when(
          data: (matches) {
            if (matches.isEmpty) return const SizedBox.shrink();
            final sessions = <String, List<FieldEvent>>{};
            for (final match in matches) {
              sessions.putIfAbsent(match.sessionName ?? 'Matches', () => []).add(match);
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final MapEntry(key: session, value: sessionMatches) in sessions.entries) ...[
                  const SizedBox(height: 20),
                  Text(session, style: AppTextStyles.cardTitle(color: AppColors.violet2)),
                  const SizedBox(height: 8),
                  for (final match in sessionMatches) ...[
                    _MatchRow(sport: sport, match: match),
                    const SizedBox(height: 8),
                  ],
                ],
              ],
            );
          },
          loading: () => const Padding(
            padding: EdgeInsets.all(24),
            child: Center(child: CircularProgressIndicator()),
          ),
          error: (error, _) => Padding(
            padding: const EdgeInsets.only(top: 20),
            child: Text('Couldn\'t load matches: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
        );
  }
}

/// "1:05 PM" in the viewer's own local time -- '' if matchTime is absent.
String _teeTimeLabel(FieldEvent match) {
  final local = match.matchTime != null ? DateTime.tryParse(match.matchTime!)?.toLocal() : null;
  return local == null ? '' : clockLabel(local);
}

class _MatchRow extends StatelessWidget {
  const _MatchRow({required this.sport, required this.match});

  final String sport;
  final FieldEvent match;

  @override
  Widget build(BuildContext context) {
    final completed = match.status == EventStatus.completed;
    final winner = match.participants.where((p) => p.result?.won ?? false).firstOrNull;
    final halved = match.participants.any((p) => p.result?.halved ?? false);
    final statusLabel = !completed
        ? _teeTimeLabel(match)
        : halved
            ? 'Halved'
            : winner?.result?.marginDisplay ?? 'FINAL';

    return InkWell(
      onTap: () => context.push(AppRoutes.eventDetail(sport, match.eventId)),
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final side in match.participants)
                    _SideLine(side: side, isWinner: identical(side, winner)),
                ],
              ),
            ),
            const SizedBox(width: 12),
            Text(statusLabel, style: AppTextStyles.microLabel(color: completed ? AppColors.inkSub : AppColors.violet)),
          ],
        ),
      ),
    );
  }
}

class _SideLine extends StatelessWidget {
  const _SideLine({required this.side, required this.isWinner});

  final FieldParticipant side;
  final bool isWinner;

  @override
  Widget build(BuildContext context) {
    final team = side.abbreviation ?? side.name ?? '';
    final golfers = side.golferNames.join(' / ');
    final label = golfers.isEmpty ? team : '$golfers ($team)';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Text(label, style: AppTextStyles.body(color: isWinner ? AppColors.ink : AppColors.inkMid)),
    );
  }
}
