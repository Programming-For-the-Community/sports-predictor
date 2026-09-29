import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/season_projection.dart';

void main() {
  test('parses standings and leaderboards from a full response', () {
    final season = SeasonProjection.fromJson({
      'sport': 'nfl',
      'season': 2026,
      'standings': [
        {
          'team_id': '12',
          'wins': 8,
          'losses': 2,
          'ties': 0,
          'projected_wins': 13.4,
          'projected_losses': 3.6,
          'division_winner_probability': 0.91,
          'playoff_probability': 0.98,
          'championship_probability': 0.22,
        },
      ],
      'leaderboards': {
        'passing_yards': [
          {'entity_id': '3139477', 'name': 'Patrick Mahomes', 'current_total': 2100.0, 'projected_total': 4300.0},
        ],
      },
      'generated_at': '2026-08-03T00:00:00Z',
    });

    expect(season.season, 2026);
    expect(season.standings.single.teamId, '12');
    expect(season.standings.single.projectedWins, 13.4);
    expect(season.leaderboards!['passing_yards']!.single.displayName, 'Patrick Mahomes');
  });

  test('ties/projected_losses default rather than throw when a stale payload omits them', () {
    // The season projection is a weekly-precomputed S3 payload, so a
    // frontend deploy can land before the backend has next produced
    // these two fields.
    final standing = TeamStanding.fromJson({
      'team_id': '12',
      'wins': 8,
      'losses': 2,
      'projected_wins': 13.4,
      'division_winner_probability': 0.91,
      'playoff_probability': 0.98,
      'championship_probability': 0.22,
    });

    expect(standing.ties, 0);
    expect(standing.projectedLosses, 0.0);
  });

  test('a standing with no simulation fields yet defaults instead of throwing', () {
    // NCAAFB's build_season_projection skips simulation entirely (fewer
    // than CFP_FIELD_SIZE teams tracked, or no promoted ranking model
    // yet) but still writes wins/losses/conference for every team. See
    // TeamStanding.fromJson's own doc comment.
    final standing = TeamStanding.fromJson({'team_id': '333', 'conference': 'SEC', 'wins': 3, 'losses': 1});

    expect(standing.division, 'SEC');
    expect(standing.projectedWins, 3.0);
    expect(standing.projectedLosses, 0.0);
    expect(standing.divisionWinnerProbability, 0.0);
    expect(standing.playoffProbability, 0.0);
    expect(standing.championshipProbability, 0.0);
  });

  test('current_rank (real) and model_rank (the ranking model) parse as separate fields', () {
    final standing = TeamStanding.fromJson({
      'team_id': '333', 'conference': 'SEC', 'wins': 8, 'losses': 1,
      'current_rank': 3, 'model_rank': 5,
    });

    expect(standing.currentRank, 3);
    expect(standing.modelRank, 5);
  });

  test('conference_champion_probability (NCAAFB) is read the same as division_winner_probability (NFL)', () {
    final standing = TeamStanding.fromJson({
      'team_id': '333', 'conference': 'SEC', 'wins': 8, 'losses': 1,
      'projected_wins': 10.2, 'conference_champion_probability': 0.35,
    });

    expect(standing.divisionWinnerProbability, 0.35);
  });

  test('leaderboards is null when the backend could not compute it', () {
    final season = SeasonProjection.fromJson({
      'sport': 'nfl',
      'season': 2026,
      'standings': [],
      'leaderboards': null,
      'generated_at': '2026-08-03T00:00:00Z',
    });

    expect(season.leaderboards, isNull);
    expect(season.standings, isEmpty);
  });

  test('leaderboard entry falls back to entity_id when name is missing', () {
    final entry = LeaderboardEntry.fromJson({
      'entity_id': '3139477',
      'current_total': 100.0,
      'projected_total': 200.0,
    });

    expect(entry.displayName, '3139477');
  });

  test('a bracket matchup with wins_a/wins_b parses as a series', () {
    final matchup = BracketMatchup.fromJson({
      'team_a': '2', 'team_b': '8', 'seed_a': 1, 'seed_b': 8, 'status': 'scheduled',
      'predicted_winner': '2', 'win_probability': 0.81, 'wins_a': 2, 'wins_b': 1,
    });

    expect(matchup.isSeries, isTrue);
    expect(matchup.winsA, 2);
    expect(matchup.winsB, 1);
  });

  test('a bracket matchup with no wins_a/wins_b is not a series (NFL/NCAAFB, NBA Play-In)', () {
    final matchup = BracketMatchup.fromJson({
      'team_a': '12', 'team_b': '13', 'status': 'projected',
      'predicted_winner': '12', 'win_probability': 0.6,
    });

    expect(matchup.isSeries, isFalse);
    expect(matchup.winsA, isNull);
    expect(matchup.winsB, isNull);
  });

  test('a bracket matchup with no status field defaults to projected instead of crashing', () {
    // cup_bracket matchups may arrive with no "status" key -- fromJson
    // must default rather than throw.
    final matchup = BracketMatchup.fromJson({
      'team_a': '2', 'team_b': '8', 'predicted_winner': '2', 'win_probability': 0.6,
    });

    expect(matchup.status, 'projected');
  });

  Map<String, dynamic> matchupJson(String a, String b) => {'team_a': a, 'team_b': b, 'status': 'final', 'actual_winner': a};

  test('parses cup, bracket, cup_bracket, march_madness_bracket and conference_brackets', () {
    final season = SeasonProjection.fromJson({
      'sport': 'nba',
      'season': 2026,
      'cup': {
        'groups': {
          'East A': [
            {'team_id': '1', 'name': 'Celtics', 'abbreviation': 'BOS', 'color': '007A33', 'group_wins': 3, 'group_losses': 1,
             'group_winner_probability': 0.7, 'knockout_probability': 0.8, 'cup_finalist_probability': 0.3,
             'champion_probability': 0.2},
            {'team_id': '2'},
          ],
        },
      },
      'bracket': {
        'conferences': {
          'East': [
            {'round': 'First Round', 'matchups': [matchupJson('1', '8')]},
          ],
        },
        'super_bowl': matchupJson('1', '9'),
        'champion': '1',
        'team_names': {
          '1': {'name': 'Celtics', 'abbreviation': 'BOS', 'color': '007A33'},
        },
      },
      'cup_bracket': {
        'rounds': [
          {'round': 'Quarterfinals', 'matchups': [matchupJson('1', '2')]},
        ],
      },
      'march_madness_bracket': {
        'first_four': [matchupJson('60', '61')],
        'regions': {
          'East': {'rounds': [{'round': 'Round of 64', 'matchups': [matchupJson('1', '60')]}], 'champion': '1'},
        },
        'final_four': [matchupJson('1', '5')],
        'championship': matchupJson('1', '9'),
        'champion': '1',
        'team_names': {'60': {'abbreviation': 'SMU'}},
      },
      'conference_brackets': [
        {'conference': 'Big East', 'bracket': {'rounds': [{'round': 'Final', 'matchups': [matchupJson('1', '2')]}]}},
      ],
    });

    final group = season.cup!.groups['East A']!;
    expect(group.first.displayName, 'BOS');
    expect(group.first.groupWins, 3);
    expect(group.first.championProbability, 0.2);
    expect(group.last.displayName, '2');
    expect(group.last.groupWinnerProbability, 0.0);

    final bracket = season.bracket!;
    expect(bracket.conferences['East']!.single.matchups.single.isFinal, isTrue);
    expect(bracket.finalMatchup!.teamB, '9');
    expect(bracket.rounds, isNull);
    expect(bracket.champion, '1');
    expect(bracket.teamNames['1']!.abbreviation, 'BOS');

    expect(season.cupBracket!.rounds!.single.round, 'Quarterfinals');
    expect(season.cupBracket!.conferences, isEmpty);
    expect(season.cupBracket!.finalMatchup, isNull);

    final mm = season.marchMadnessBracket!;
    expect(mm.firstFour.single.teamA, '60');
    expect(mm.regions['East']!.champion, '1');
    expect(mm.regions['East']!.rounds.single.round, 'Round of 64');
    expect(mm.finalFour.single.teamB, '5');
    expect(mm.championship!.teamB, '9');
    expect(mm.champion, '1');
    expect(mm.teamNames['60']!.abbreviation, 'SMU');

    expect(season.conferenceBrackets!.single.conference, 'Big East');
    expect(season.conferenceBrackets!.single.bracket.rounds!.single.round, 'Final');
  });

  test('cup displayName falls back from abbreviation to name', () {
    expect(CupTeamStanding.fromJson({'team_id': '1', 'name': 'Celtics'}).displayName, 'Celtics');
  });

  test('bracket projection reads finals/championship as the cross-conference matchup', () {
    expect(BracketProjection.fromJson({'finals': matchupJson('1', '2')}).finalMatchup!.teamB, '2');
    expect(BracketProjection.fromJson({'championship': matchupJson('3', '4')}).finalMatchup!.teamB, '4');
  });

  test('an empty march madness bracket defaults every collection', () {
    final mm = MarchMadnessBracket.fromJson({});

    expect(mm.firstFour, isEmpty);
    expect(mm.regions, isEmpty);
    expect(mm.finalFour, isEmpty);
    expect(mm.championship, isNull);
    expect(mm.teamNames, isEmpty);
  });
}
