import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/season_repository.dart';
import '../../core/models/season_projection.dart';
import '../../core/models/sport_config.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/conference_filter_field.dart';
import '../../core/widgets/responsive.dart';
import '../../core/widgets/status_toggle.dart';
import '../../static/conference_order.dart';
import 'season_bracket_section.dart';
import 'season_conference_brackets_section.dart';
import 'season_leaderboards.dart';
import 'season_march_madness_section.dart';
import 'season_standings_table.dart';

/// NBA standings group by conference only; every other sport groups by
/// division.
String _standingsGroupKey(String sport, String? division) {
  final raw = division ?? 'Other';
  if (sport != SportIds.nba) return raw;
  final firstWord = raw.split(' ').first;
  return firstWord.isEmpty ? raw : firstWord;
}

/// Buckets standings by division (or NBA conference), preserving each
/// team's relative order. `filter`, when non-empty, keeps only groups whose
/// name contains it (case-insensitive).
List<MapEntry<String, List<TeamStanding>>> _groupByDivision(String sport, List<TeamStanding> standings, String filter) {
  final byDivision = <String, List<TeamStanding>>{};
  for (final team in standings) {
    byDivision.putIfAbsent(_standingsGroupKey(sport, team.division), () => []).add(team);
  }
  final needle = filter.trim().toLowerCase();
  final divisions = byDivision.keys.where((d) => needle.isEmpty || d.toLowerCase().contains(needle)).toList()
    ..sort(compareConferenceOrder);
  return [for (final division in divisions) MapEntry(division, byDivision[division]!)];
}

// Internal-only view-selector values -- never sent to or received from
// the backend, not shared with any other file, but named instead of
// typed inline at each of the toggle/branch sites below.
abstract final class _SeasonTab {
  static const standings = 'standings';
  static const props = 'props';
  static const bracket = 'bracket';
  static const cupBracket = 'cup_bracket';
  static const marchMadness = 'march_madness';
  static const conferenceBrackets = 'conference_brackets';
}

abstract final class _SeasonTabLabels {
  static const standings = 'Standings & Playoff Odds';
  static const props = 'Player Prop Leaders';
  static const bracket = 'Playoff Bracket';
  static const cupBracket = 'NBA Cup Bracket';
  static const marchMadness = 'March Madness';
  static const conferenceBrackets = 'Conference Brackets';
}

class SeasonPage extends ConsumerStatefulWidget {
  const SeasonPage({super.key, required this.sportId});

  final String sportId;

  @override
  ConsumerState<SeasonPage> createState() => _SeasonPageState();
}

class _SeasonPageState extends ConsumerState<SeasonPage> {
  String _tab = _SeasonTab.standings;
  String _conferenceFilter = '';

  // Toggle options only appear when the backend sent that block. Horizontal-
  // scroll Row -- the toggle labels don't fit a phone-width screen. Null
  // when the backend sent none of the optional blocks, so build() knows to
  // skip both this row and the spacing after it.
  Widget? _tabToggleRow(SeasonProjection season) {
    final hasAnyOptionalBlock = season.leaderboards != null ||
        season.bracket != null ||
        season.cupBracket != null ||
        season.marchMadnessBracket != null ||
        season.conferenceBrackets != null;
    if (!hasAnyOptionalBlock) return null;

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          StatusToggle(
            label: _SeasonTabLabels.standings,
            selected: _tab == _SeasonTab.standings,
            onTap: () => setState(() => _tab = _SeasonTab.standings),
            accentColor: AppColors.cyan,
          ),
          if (season.leaderboards != null) ...[
            const SizedBox(width: 8),
            StatusToggle(
              label: _SeasonTabLabels.props,
              selected: _tab == _SeasonTab.props,
              onTap: () => setState(() => _tab = _SeasonTab.props),
              accentColor: AppColors.cyan,
            ),
          ],
          if (season.bracket != null) ...[
            const SizedBox(width: 8),
            StatusToggle(
              label: _SeasonTabLabels.bracket,
              selected: _tab == _SeasonTab.bracket,
              onTap: () => setState(() => _tab = _SeasonTab.bracket),
              accentColor: AppColors.cyan,
            ),
          ],
          if (season.cupBracket != null) ...[
            const SizedBox(width: 8),
            StatusToggle(
              label: _SeasonTabLabels.cupBracket,
              selected: _tab == _SeasonTab.cupBracket,
              onTap: () => setState(() => _tab = _SeasonTab.cupBracket),
              accentColor: AppColors.cyan,
            ),
          ],
          if (season.marchMadnessBracket != null) ...[
            const SizedBox(width: 8),
            StatusToggle(
              label: _SeasonTabLabels.marchMadness,
              selected: _tab == _SeasonTab.marchMadness,
              onTap: () => setState(() => _tab = _SeasonTab.marchMadness),
              accentColor: AppColors.cyan,
            ),
          ],
          if (season.conferenceBrackets != null) ...[
            const SizedBox(width: 8),
            StatusToggle(
              label: _SeasonTabLabels.conferenceBrackets,
              selected: _tab == _SeasonTab.conferenceBrackets,
              onTap: () => setState(() => _tab = _SeasonTab.conferenceBrackets),
              accentColor: AppColors.cyan,
            ),
          ],
        ],
      ),
    );
  }

  Widget _standingsView(SeasonProjection season) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Only shown when there's more than one conference/division to
        // filter.
        if (_groupByDivision(season.sport, season.standings, '').length > 1) ...[
          ConferenceFilterField(
            value: _conferenceFilter,
            onChanged: (value) => setState(() => _conferenceFilter = value),
          ),
          const SizedBox(height: 16),
        ],
        // Fixed-width division cards in a Wrap so multiple divisions fit
        // per row on a wide screen.
        LayoutBuilder(
          builder: (context, constraints) {
            final width = cardWidth(
              season.sport == SportIds.ncaafb || season.sport == SportIds.ncaambb ? 560 : 480, constraints.maxWidth,
            );
            final divisions = _groupByDivision(season.sport, season.standings, _conferenceFilter);
            if (divisions.isEmpty) {
              return Text('No conferences match "$_conferenceFilter".', style: AppTextStyles.body(color: AppColors.inkSub));
            }
            return Wrap(
              spacing: 20,
              runSpacing: 20,
              children: [
                for (final division in divisions)
                  SizedBox(
                    width: width,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(division.key.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.cyan)),
                        ),
                        StandingsTable(sport: season.sport, standings: division.value),
                      ],
                    ),
                  ),
              ],
            );
          },
        ),
      ],
    );
  }

  Widget _tabContent(SeasonProjection season) {
    if (_tab == _SeasonTab.props && season.leaderboards != null) {
      return SeasonLeaderboards(leaderboards: season.leaderboards);
    }
    if (_tab == _SeasonTab.bracket && season.bracket != null) {
      return BracketSection(sport: season.sport, bracket: season.bracket!);
    }
    if (_tab == _SeasonTab.cupBracket && season.cupBracket != null) {
      return BracketSection(sport: season.sport, bracket: season.cupBracket!);
    }
    if (_tab == _SeasonTab.marchMadness && season.marchMadnessBracket != null) {
      return MarchMadnessSection(sport: season.sport, bracket: season.marchMadnessBracket!);
    }
    if (_tab == _SeasonTab.conferenceBrackets && season.conferenceBrackets != null) {
      return ConferenceBracketsSection(sport: season.sport, conferenceBrackets: season.conferenceBrackets!);
    }
    return _standingsView(season);
  }

  @override
  Widget build(BuildContext context) {
    final projection = ref.watch(seasonProjectionProvider(widget.sportId));

    return RefreshIndicator(
      onRefresh: () => ref.refresh(seasonProjectionProvider(widget.sportId).future),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
      child: projection.when(
        data: (season) {
          final toggleRow = _tabToggleRow(season);
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                season.season != null ? '${season.season} Season' : 'Season',
                style: AppTextStyles.pageH1(),
              ),
              const SizedBox(height: 20),
              if (toggleRow != null) ...[toggleRow, const SizedBox(height: 20)],
              _tabContent(season),
            ],
          );
        },
        loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
        error: (error, _) =>
            Text('Couldn\'t load season projection: $error', style: AppTextStyles.body(color: AppColors.neg)),
      ),
      ),
    );
  }
}
