import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/season_repository.dart';
import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/features/season/season_page.dart';

/// Functional (not just overflow) coverage for the season page's 3-way
/// tab logic (standings/player-prop-leaders/NBA-Cup) and NBA's own
/// standings column set -- season_page_mobile_test.dart only asserts "no
/// overflow", not which content actually renders.
TeamStanding _nbaStanding(String teamId, {double playInProbability = 0.1}) => TeamStanding(
      teamId: teamId,
      division: 'Eastern Atlantic',
      wins: 10,
      losses: 5,
      ties: 0,
      projectedWins: 55.0,
      projectedLosses: 27.0,
      divisionWinnerProbability: 0.2,
      playoffProbability: 0.7,
      championshipProbability: 0.05,
      playInProbability: playInProbability,
    );

void main() {
  Future<void> pumpSeasonPage(WidgetTester tester, String sportId, SeasonProjection projection) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [seasonProjectionProvider.overrideWith((ref, sport) async => projection)],
        child: MaterialApp(home: Scaffold(body: SeasonPage(sportId: sportId))),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('nba standings show PLAY-IN% instead of DIV%', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba', season: 2026, standings: [_nbaStanding('2')], leaderboards: null,
    );

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('PLAY-IN%'), findsOneWidget);
    expect(find.text('DIV%'), findsNothing);
    expect(find.text('PLAYOFFS%'), findsOneWidget);
    expect(find.text('CHAMP%'), findsOneWidget);
  });

  testWidgets('nfl standings keep DIV%/PO%/SB%, no PLAY-IN%', (tester) async {
    final projection = SeasonProjection(
      sport: 'nfl',
      season: 2025,
      standings: [
        TeamStanding(
          teamId: '12', division: 'AFC West', wins: 10, losses: 5, ties: 0,
          projectedWins: 12.0, projectedLosses: 5.0,
          divisionWinnerProbability: 0.6, playoffProbability: 0.8, championshipProbability: 0.1,
        ),
      ],
      leaderboards: null,
    );

    await pumpSeasonPage(tester, 'nfl', projection);

    expect(find.text('DIV%'), findsOneWidget);
    expect(find.text('PLAY-IN%'), findsNothing);
  });

  testWidgets('nba standings group by conference only, not the 3 divisions within each', (tester) async {
    // Real NBA standings convention (unlike NFL's by-division grouping) --
    // 2 teams from different divisions in the same conference collapse
    // into one "EASTERN" group, not two ("EASTERN ATLANTIC"/"EASTERN
    // CENTRAL") -- see _standingsGroupKey's own docstring.
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [
        TeamStanding(
          teamId: '2', division: 'Eastern Atlantic', wins: 10, losses: 5, ties: 0,
          projectedWins: 55.0, projectedLosses: 27.0,
          divisionWinnerProbability: 0.2, playoffProbability: 0.7, championshipProbability: 0.05,
        ),
        TeamStanding(
          teamId: '4', division: 'Eastern Central', wins: 8, losses: 7, ties: 0,
          projectedWins: 45.0, projectedLosses: 37.0,
          divisionWinnerProbability: 0.1, playoffProbability: 0.5, championshipProbability: 0.02,
        ),
        TeamStanding(
          teamId: '7', division: 'Western Northwest', wins: 12, losses: 3, ties: 0,
          projectedWins: 60.0, projectedLosses: 22.0,
          divisionWinnerProbability: 0.3, playoffProbability: 0.8, championshipProbability: 0.08,
        ),
      ],
      leaderboards: null,
    );

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('EASTERN'), findsOneWidget);
    expect(find.text('WESTERN'), findsOneWidget);
    expect(find.text('EASTERN ATLANTIC'), findsNothing);
    expect(find.text('EASTERN CENTRAL'), findsNothing);
    expect(find.text('WESTERN NORTHWEST'), findsNothing);
  });

  testWidgets('no toggle row at all when neither leaderboards nor bracket nor cup bracket is present', (tester) async {
    final projection = SeasonProjection(sport: 'nba', season: 2026, standings: [_nbaStanding('2')], leaderboards: null);

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('Standings & Playoff Odds'), findsNothing);
    expect(find.text('NBA Cup'), findsNothing);
  });

  testWidgets(
      "the NBA Cup group-standings tab is gone -- cup's own bracket tab covers the same tournament without it",
      (tester) async {
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [_nbaStanding('2')],
      leaderboards: null,
      // `cup` (group standings) still exists on the payload -- see
      // season_page.dart's own comment on why -- but must not surface a
      // tab of its own anymore, even when present.
      cup: const CupProjection(groups: {
        'Eastern B': [
          CupTeamStanding(
            teamId: '2', abbreviation: 'BOS', groupWins: 4, groupLosses: 1,
            groupWinnerProbability: 0.6, knockoutProbability: 0.7, cupFinalistProbability: 0.3, championProbability: 0.1,
          ),
        ],
      }),
      cupBracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Semifinals', matchups: [
              BracketMatchup(teamA: '2', teamB: '8', seedA: 1, seedB: 4, status: 'projected', predictedWinner: '2', winProbability: 0.6),
            ]),
          ],
        },
        rounds: null,
        teamNames: {'2': BracketTeamName(name: 'Boston Celtics', abbreviation: 'BOS')},
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);

    expect(find.text('NBA Cup'), findsNothing);
    expect(find.text('NBA Cup Bracket'), findsOneWidget);
    // Standings tab is the default -- the Cup bracket isn't visible yet.
    expect(find.text('SEMIFINALS'), findsNothing);

    await tester.tap(find.text('NBA Cup Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('SEMIFINALS'), findsOneWidget);
    expect(find.text('BOS'), findsOneWidget);
    // The group-standings-only content (record/ADV%/CHAMP% columns) must
    // not appear anywhere -- confirms _CupSection is truly gone, not just
    // unreachable via the toggle.
    expect(find.text('ADV%'), findsNothing);
  });

  testWidgets('Playoff Bracket toggle appears when bracket is present and switches to the bracket view', (tester) async {
    final projection = SeasonProjection(
      sport: 'nfl',
      season: 2026,
      standings: [_nbaStanding('2')], // reused fixture, sport-neutral shape
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'AFC': [
            BracketRound(round: 'Wild Card', matchups: [
              BracketMatchup(
                teamA: '12', teamB: '13', seedA: 2, seedB: 7, status: 'projected',
                predictedWinner: '12', winProbability: 0.62,
              ),
            ]),
          ],
          'NFC': [
            BracketRound(round: 'Wild Card', matchups: [
              BracketMatchup(
                teamA: '19', teamB: '24', seedA: 2, seedB: 7, status: 'projected',
                predictedWinner: '19', winProbability: 0.55,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
        finalMatchup: BracketMatchup(teamA: '12', teamB: '19', status: 'projected', predictedWinner: '12', winProbability: 0.51),
        champion: '12',
      ),
    );

    await pumpSeasonPage(tester, 'nfl', projection);

    expect(find.text('Playoff Bracket'), findsOneWidget);
    // Standings tab is the default -- bracket content isn't visible yet.
    expect(find.text('AFC'), findsNothing);

    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('AFC'), findsOneWidget);
    expect(find.text('NFC'), findsOneWidget);
    expect(find.text('SUPER BOWL'), findsOneWidget);
    // Both conferences now share one combined tree (see _BracketSection's
    // own docstring) -- "Wild Card" is one round column holding both
    // conferences' matchups, not two separate trees with their own
    // independent round headers, so the label itself appears once.
    expect(find.text('WILD CARD'), findsOneWidget);
  });

  testWidgets('a flat (NCAAFB-shaped) bracket renders its rounds with no conference headers', (tester) async {
    final projection = SeasonProjection(
      sport: 'ncaafb',
      season: 2025,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {},
        rounds: [
          BracketRound(round: 'Round of 12', matchups: [
            BracketMatchup(teamA: '5', teamB: '12', seedA: 5, seedB: 12, status: 'projected', predictedWinner: '5', winProbability: 0.7),
          ]),
          BracketRound(round: 'National Championship', matchups: [
            BracketMatchup(teamA: '1', teamB: '2', seedA: 1, seedB: 2, status: 'projected', predictedWinner: '1', winProbability: 0.58),
          ]),
        ],
        teamNames: {},
        champion: '1',
      ),
    );

    await pumpSeasonPage(tester, 'ncaafb', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('ROUND OF 12'), findsOneWidget);
    expect(find.text('NATIONAL CHAMPIONSHIP'), findsOneWidget);
    expect(find.text('CHAMPIONSHIP'), findsNothing); // that label is only the conference-split final's own header
    // No skip connection exists in this bracket -- the legend explaining
    // one shouldn't show up for a sport that never has one.
    expect(find.textContaining('skipping the Elimination Game'), findsNothing);
  });

  testWidgets('a completed bracket matchup shows the real score, not a probability', (tester) async {
    final projection = SeasonProjection(
      sport: 'nfl',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'AFC': [
            BracketRound(round: 'Wild Card', matchups: [
              BracketMatchup(
                teamA: '12', teamB: '13', seedA: 2, seedB: 7, status: 'final',
                predictedWinner: '12', winProbability: 0.62,
                actualWinner: '12', actualHomeScore: 27, actualAwayScore: 13,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
      ),
    );

    await pumpSeasonPage(tester, 'nfl', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('FINAL'), findsOneWidget);
    expect(find.text('27'), findsOneWidget);
    expect(find.text('13'), findsOneWidget);
  });

  testWidgets('an NBA series matchup shows the live win-loss record, not a single score', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(
                teamA: '2', teamB: '8', seedA: 1, seedB: 8, status: 'scheduled',
                predictedWinner: '2', winProbability: 0.81, winsA: 2, winsB: 1,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {
          '2': BracketTeamName(name: 'Boston Celtics', abbreviation: 'BOS'),
          '8': BracketTeamName(name: 'Detroit Pistons', abbreviation: 'DET'),
        },
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // The series record (2-1), not a raw box score.
    expect(find.textContaining('2-1'), findsOneWidget);
    expect(find.text('BOS'), findsOneWidget);
    expect(find.text('DET'), findsOneWidget);
  });

  testWidgets('a decided NBA series (4 wins) shows as final with the series record', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(
                teamA: '2', teamB: '8', seedA: 1, seedB: 8, status: 'final',
                actualWinner: '2', winsA: 4, winsB: 2,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {
          '2': BracketTeamName(name: 'Boston Celtics', abbreviation: 'BOS'),
          '8': BracketTeamName(name: 'Detroit Pistons', abbreviation: 'DET'),
        },
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.textContaining('BOS WINS SERIES 4-2'), findsOneWidget);
  });

  testWidgets(
      "a not-yet-real NBA series matchup's status line still shows its 0-0 record -- regression "
      'for the PROJECTED case dropping it while scheduled/final both showed it', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(
                teamA: '2', teamB: '8', seedA: 1, seedB: 8, status: 'projected',
                predictedWinner: '2', winProbability: 0.7, winsA: 0, winsB: 0,
                predictedWinsA: 4, predictedWinsB: 2,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {
          '2': BracketTeamName(name: 'Boston Celtics', abbreviation: 'BOS'),
          '8': BracketTeamName(name: 'Detroit Pistons', abbreviation: 'DET'),
        },
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.textContaining('0-0'), findsOneWidget);
    expect(find.textContaining('PROJECTED'), findsOneWidget);
    // The predicted FINAL record (4-2, BOS the predicted winner), distinct
    // from the 0-0 current record above.
    expect(find.textContaining('predicted 4-2'), findsOneWidget);
  });

  testWidgets(
      "a scheduled (real, in-progress) NBA series shows its predicted final record alongside the "
      'live one', (tester) async {
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(
                teamA: '2', teamB: '8', seedA: 1, seedB: 8, status: 'scheduled',
                predictedWinner: '2', winProbability: 0.81, winsA: 2, winsB: 1,
                predictedWinsA: 4, predictedWinsB: 1,
              ),
            ]),
          ],
        },
        rounds: null,
        teamNames: {
          '2': BracketTeamName(name: 'Boston Celtics', abbreviation: 'BOS'),
          '8': BracketTeamName(name: 'Detroit Pistons', abbreviation: 'DET'),
        },
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // The live 2-1 record and the predicted 4-1 final are both shown --
    // distinct pieces of information, not conflated into one number.
    expect(find.textContaining('2-1'), findsOneWidget);
    expect(find.textContaining('predicted 4-1'), findsOneWidget);
  });

  testWidgets(
      'NBA Play-In feeding Conference Quarterfinals lays out every card at a distinct '
      'vertical slot -- regression for a real overlapping-card bug', (tester) async {
    // Reproduces the exact shape that overlapped: Play-In's 3 games feed
    // a 4-matchup Conference Quarterfinals round where 2 matchups (seeds
    // 4v5, 3v6) are byes with no traceable Play-In winner. The old
    // fallback positioned '203 vs 206' at the same slot as '201 vs 208'
    // (both landed on slot 2) because the naive index fallback didn't
    // account for a slot a traced matchup elsewhere in the round had
    // already claimed.
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Play-In', matchups: [
              BracketMatchup(teamA: '207', teamB: '208', status: 'projected', predictedWinner: '207', winProbability: 0.6),
              BracketMatchup(teamA: '209', teamB: '210', status: 'projected', predictedWinner: '209', winProbability: 0.6),
              BracketMatchup(teamA: '208', teamB: '209', status: 'projected', predictedWinner: '208', winProbability: 0.6),
            ]),
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(teamA: '201', teamB: '208', status: 'projected', predictedWinner: '201', winProbability: 0.7),
              BracketMatchup(teamA: '204', teamB: '205', status: 'projected', predictedWinner: '204', winProbability: 0.55),
              BracketMatchup(teamA: '203', teamB: '206', status: 'projected', predictedWinner: '203', winProbability: 0.55),
              BracketMatchup(teamA: '202', teamB: '207', status: 'projected', predictedWinner: '202', winProbability: 0.6),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // Every Conference Quarterfinals card's own team-id text (no
    // teamNames map supplied, so teamDisplayFor falls back to the raw
    // id) should sit at a unique vertical position -- the specific pair
    // that used to collide is '201'/'203', asserted explicitly, plus a
    // blanket uniqueness check across the whole round for good measure.
    final quarterfinalIds = ['201', '204', '203', '202'];
    final tops = {for (final id in quarterfinalIds) id: tester.getTopLeft(find.text(id)).dy};

    expect(tops['201'], isNot(equals(tops['203'])));
    expect(tops.values.toSet().length, quarterfinalIds.length);
  });

  testWidgets(
      'adjacent Quarterfinal cards stay a full card-height apart even when one lands on a '
      'fractional slot -- regression for a real visual overlap distinct-value checks missed',
      (tester) async {
    // The Play-In-split shape (see the test below) puts the (1v8)
    // Quarterfinal at slot 0.5 (the average of Play-In Elimination's own
    // 2-source trace) right next to (4v5)'s bye fallback at slot 1.0 --
    // two DISTINCT slot values, so the earlier "isNot(equals(...))"-style
    // checks passed, but only half a row apart, and a card is taller
    // than half a row: the cards physically overlapped in production
    // despite never computing the same slot.
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Play-In', matchups: [
              BracketMatchup(teamA: '209', teamB: '210', status: 'projected', predictedWinner: '209', winProbability: 0.6),
              BracketMatchup(teamA: '207', teamB: '208', status: 'projected', predictedWinner: '207', winProbability: 0.6),
            ]),
            BracketRound(round: 'Play-In Elimination', matchups: [
              BracketMatchup(teamA: '208', teamB: '209', status: 'projected', predictedWinner: '208', winProbability: 0.6),
            ]),
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(teamA: '201', teamB: '208', status: 'projected', predictedWinner: '201', winProbability: 0.7),
              BracketMatchup(teamA: '204', teamB: '205', status: 'projected', predictedWinner: '204', winProbability: 0.55),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // Card height is 108px (_BracketTree's own _cardHeight) -- two
    // vertically-stacked cards must be at least that far apart or they
    // overlap, regardless of whether their slot VALUES happen to differ.
    final topGap = tester.getTopLeft(find.text('204')).dy - tester.getTopLeft(find.text('201')).dy;
    expect(topGap, greaterThanOrEqualTo(108));
  });

  testWidgets(
      'NBA Conference Semifinals also lay out at distinct vertical slots -- regression for a '
      'real overlapping-card bug the Quarterfinals-only fix did not cover', (tester) async {
    // Same Play-In/Quarterfinals shape as the regression test above, one
    // round further. The Quarterfinals fix only dedups a slot AGAINST an
    // already-traced one -- it never revisits a slot once assigned. Here
    // QF2 (203v206, a bye) gets nudged from its natural slot 2 to 3 to
    // avoid QF0's traced slot 2, which makes SF1's average ((QF2=3 +
    // QF3=0) / 2 = 1.5) coincidentally equal SF0's average ((QF0=2 +
    // QF1=1) / 2 = 1.5) -- two different Semifinal matchups, neither of
    // them a bye, landing on the same slot.
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Play-In', matchups: [
              BracketMatchup(teamA: '207', teamB: '208', status: 'projected', predictedWinner: '207', winProbability: 0.6),
              BracketMatchup(teamA: '209', teamB: '210', status: 'projected', predictedWinner: '209', winProbability: 0.6),
              BracketMatchup(teamA: '208', teamB: '209', status: 'projected', predictedWinner: '208', winProbability: 0.6),
            ]),
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(teamA: '201', teamB: '208', status: 'projected', predictedWinner: '201', winProbability: 0.7),
              BracketMatchup(teamA: '204', teamB: '205', status: 'projected', predictedWinner: '204', winProbability: 0.55),
              BracketMatchup(teamA: '203', teamB: '206', status: 'projected', predictedWinner: '203', winProbability: 0.55),
              BracketMatchup(teamA: '202', teamB: '207', status: 'projected', predictedWinner: '202', winProbability: 0.6),
            ]),
            BracketRound(round: 'Conference Semifinals', matchups: [
              BracketMatchup(teamA: '201', teamB: '204', status: 'projected', predictedWinner: '201', winProbability: 0.65),
              BracketMatchup(teamA: '203', teamB: '202', status: 'projected', predictedWinner: '203', winProbability: 0.52),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // '201' and '203' each appear twice (Quarterfinals, then Semifinals as
    // the advancing winner) -- the second occurrence is the Semifinal card.
    final sf0Top = tester.getTopLeft(find.text('201').at(1)).dy;
    final sf1Top = tester.getTopLeft(find.text('203').at(1)).dy;
    expect(sf0Top, isNot(equals(sf1Top)));
  });

  testWidgets(
      "one conference's Quarterfinals stay in their own (1v8)/(4v5)/(3v6)/(2v7) seed order even "
      "when the other conference's bye fallback numerically collides with it -- regression for a "
      'real cross-conference slot-stealing bug', (tester) async {
    // Both conferences use the exact same shape (byes at local round
    // positions 1 and 2), which is exactly what let Western's bye
    // fallback land on the same slot Eastern's own traced matchup used
    // when everything was laid out on one shared, concatenated round --
    // silently swapping Western's own last two Quarterfinal cards.
    // game3 first, game1 last -- matches project_play_in's own backend
    // ordering (see season_simulation.py), not the raw game-number order.
    BracketRound playIn(String p) => BracketRound(round: 'Play-In', matchups: [
          BracketMatchup(teamA: '${p}08', teamB: '${p}09', status: 'projected', predictedWinner: '${p}08', winProbability: 0.6),
          BracketMatchup(teamA: '${p}09', teamB: '${p}10', status: 'projected', predictedWinner: '${p}09', winProbability: 0.6),
          BracketMatchup(teamA: '${p}07', teamB: '${p}08', status: 'projected', predictedWinner: '${p}07', winProbability: 0.6),
        ]);
    BracketRound quarterfinals(String p) => BracketRound(round: 'Conference Quarterfinals', matchups: [
          BracketMatchup(teamA: '${p}01', teamB: '${p}08', status: 'projected', predictedWinner: '${p}01', winProbability: 0.7),
          BracketMatchup(teamA: '${p}04', teamB: '${p}05', status: 'projected', predictedWinner: '${p}04', winProbability: 0.55),
          BracketMatchup(teamA: '${p}03', teamB: '${p}06', status: 'projected', predictedWinner: '${p}03', winProbability: 0.55),
          BracketMatchup(teamA: '${p}02', teamB: '${p}07', status: 'projected', predictedWinner: '${p}02', winProbability: 0.6),
        ]);

    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: BracketProjection(
        conferences: {
          'Eastern': [playIn('2'), quarterfinals('2')],
          'Western': [playIn('3'), quarterfinals('3')],
        },
        rounds: null,
        teamNames: const {},
        finalMatchup: const BracketMatchup(teamA: '201', teamB: '301', status: 'projected', predictedWinner: '201', winProbability: 0.5),
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // Western's own Quarterfinals must read top-to-bottom in the same
    // canonical (1v8)/(4v5)/(3v6)/(2v7) order Eastern's already-passing
    // regression test above confirms -- '303' (3v6) must sit above '302'
    // (2v7), never swapped by Eastern's own layout.
    // '301' also appears again in the Championship card (it's
    // finalMatchup's teamA) -- .first grabs its earlier Quarterfinals
    // occurrence, matching document order (rounds render left to right).
    final westernIds = ['301', '304', '303', '302'];
    final tops = {for (final id in westernIds) id: tester.getTopLeft(find.text(id).first).dy};
    expect(tops['303'], lessThan(tops['302']!));
    expect(tops.values.toSet().length, westernIds.length);
  });

  testWidgets(
      'NBA Play-In splits into two rounds (games 1/2, then the Elimination Game built from their '
      "results) and Conference Quarterfinals still lands in canonical (1v8)/(4v5)/(3v6)/(2v7) order "
      '-- regression for a real "all 3 Play-In games look like the same round" complaint', (tester) async {
    // Game 1 (207v208) and Game 2 (209v210) are independent -- Game 3
    // (208v209) is built FROM their results (game1's loser vs game2's
    // winner) and belongs one round later. Game 1's own winner ('207')
    // skips the Elimination round entirely and reappears two rounds
    // later, directly in the Quarterfinals' own (2v7) pairing -- the
    // exact shape that used to scramble Quarterfinal order when a
    // further-back source was allowed to drive vertical position.
    final projection = SeasonProjection(
      sport: 'nba',
      season: 2026,
      standings: [],
      leaderboards: null,
      bracket: const BracketProjection(
        conferences: {
          'Eastern': [
            BracketRound(round: 'Play-In', matchups: [
              BracketMatchup(teamA: '209', teamB: '210', status: 'projected', predictedWinner: '209', winProbability: 0.6),
              BracketMatchup(teamA: '207', teamB: '208', status: 'projected', predictedWinner: '207', winProbability: 0.6),
            ]),
            BracketRound(round: 'Play-In Elimination', matchups: [
              BracketMatchup(teamA: '208', teamB: '209', status: 'projected', predictedWinner: '208', winProbability: 0.6),
            ]),
            BracketRound(round: 'Conference Quarterfinals', matchups: [
              BracketMatchup(teamA: '201', teamB: '208', status: 'projected', predictedWinner: '201', winProbability: 0.7),
              BracketMatchup(teamA: '204', teamB: '205', status: 'projected', predictedWinner: '204', winProbability: 0.55),
              BracketMatchup(teamA: '203', teamB: '206', status: 'projected', predictedWinner: '203', winProbability: 0.55),
              BracketMatchup(teamA: '202', teamB: '207', status: 'projected', predictedWinner: '202', winProbability: 0.6),
            ]),
          ],
        },
        rounds: null,
        teamNames: {},
      ),
    );

    await pumpSeasonPage(tester, 'nba', projection);
    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    // Play-In Elimination must be its own COLUMN (a later round), not
    // just visually offset -- the user's actual complaint was seeing all
    // 3 Play-In games at the same horizontal level. Compare the two
    // round headers' own x position directly.
    final playInHeaderLeft = tester.getTopLeft(find.text('PLAY-IN')).dx;
    final eliminationHeaderLeft = tester.getTopLeft(find.text('PLAY-IN ELIMINATION')).dx;
    expect(eliminationHeaderLeft, greaterThan(playInHeaderLeft));

    // Quarterfinals must read top-to-bottom in seed order -- '208'
    // (fed by the Elimination Game, one round back) and '207' (skips the
    // Elimination round, sourced two rounds back) both drive real
    // Quarterfinal positions; the two byes ('204v205', '203v206') sit
    // between them, matching (1v8)/(4v5)/(3v6)/(2v7).
    final quarterfinalIds = ['201', '204', '203', '202'];
    final tops = {for (final id in quarterfinalIds) id: tester.getTopLeft(find.text(id).last).dy};
    expect(tops['201'], lessThan(tops['204']!));
    expect(tops['204'], lessThan(tops['203']!));
    expect(tops['203'], lessThan(tops['202']!));

    // A dashed-line legend explains what the skip connection (207's own
    // winner, bypassing Play-In Elimination) means -- this fixture is
    // exactly the shape that produces one.
    expect(find.textContaining('skipping the Elimination Game'), findsOneWidget);
  });

  testWidgets('ncaafb has neither PLAY-IN% nor an NBA Cup toggle', (tester) async {
    final projection = SeasonProjection(
      sport: 'ncaafb',
      season: 2025,
      standings: [
        TeamStanding(
          teamId: '99', division: 'SEC', wins: 10, losses: 2, ties: 0,
          projectedWins: 11.0, projectedLosses: 2.0,
          divisionWinnerProbability: 0.4, playoffProbability: 0.6, championshipProbability: 0.15,
        ),
      ],
      leaderboards: null,
    );

    await pumpSeasonPage(tester, 'ncaafb', projection);

    expect(find.text('PLAY-IN%'), findsNothing);
    expect(find.text('NBA Cup'), findsNothing);
    expect(find.text('CONF%'), findsOneWidget);
  });
}
