import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/season_repository.dart';
import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/features/season/season_page.dart';

/// Functional (not just overflow) coverage for the season page's 3-way
/// tab logic (standings/player-prop-leaders/NBA-Cup) and NBA's own
/// standings column set.
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
    expect(find.text('SEMIFINALS'), findsNothing);

    await tester.tap(find.text('NBA Cup Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('SEMIFINALS'), findsOneWidget);
    expect(find.text('BOS'), findsOneWidget);
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
    expect(find.text('AFC'), findsNothing);

    await tester.tap(find.text('Playoff Bracket'));
    await tester.pumpAndSettle();

    expect(find.text('AFC'), findsOneWidget);
    expect(find.text('NFC'), findsOneWidget);
    expect(find.text('SUPER BOWL'), findsOneWidget);
    // Both conferences share one combined tree -- "Wild Card" is one round
    // column holding both conferences' matchups, so the label appears once.
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
    expect(find.text('CHAMPIONSHIP'), findsNothing); // only the conference-split final's own header
    expect(find.textContaining('skipping the Elimination Game'), findsNothing);
  });

  testWidgets('a bye matchup (null team_b) renders BYE instead of crashing', (tester) async {
    final projection = SeasonProjection(
      sport: 'ncaambb',
      season: 2027,
      standings: [],
      leaderboards: null,
      marchMadnessBracket: const MarchMadnessBracket(
        firstFour: [],
        // A single region (not 4) falls back to the independent-tree path
        // -- exercises the same _BracketTeamRow null handling without
        // needing a full 4-region fixture for this test's purpose.
        regions: {
          'Region A': RegionBracket(
            rounds: [
              BracketRound(round: 'Round of 64', matchups: [
                BracketMatchup(teamA: '9', teamB: null, seedA: 1, seedB: null, status: 'projected', predictedWinner: '9', winProbability: 1.0),
              ]),
            ],
            champion: '9',
          ),
        },
        finalFour: [],
        teamNames: {},
        champion: '9',
      ),
    );

    await pumpSeasonPage(tester, 'ncaambb', projection);
    await tester.tap(find.text('March Madness'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('BYE'), findsOneWidget);
  });

  testWidgets('a full 4-region March Madness bracket renders First Four, all 4 region labels, and the Championship, with no overlapping cards', (tester) async {
    // Region A gets a real Round of 64 (its First Four winner, '90', is
    // one of the 2 entrants of its own first matchup) so the grid can
    // actually resolve and place the First Four card; the other 3
    // regions' Round of 64 is a placeholder pair feeding the same
    // Elite Eight champion, just to keep every region's own round count
    // consistent (see _MarchMadnessGrid's own halfColumns docstring).
    RegionBracket region(String championA, String championB, String champion, {String? firstFourWinner}) => RegionBracket(
          rounds: [
            BracketRound(round: 'Round of 64', matchups: [
              BracketMatchup(
                teamA: firstFourWinner ?? championA,
                teamB: '${championA}x',
                seedA: 16,
                seedB: 1,
                status: 'projected',
                predictedWinner: championA,
                winProbability: 0.6,
              ),
              BracketMatchup(teamA: championB, teamB: '${championB}x', seedA: 8, seedB: 9, status: 'projected', predictedWinner: championB, winProbability: 0.6),
            ]),
            BracketRound(round: 'Elite Eight', matchups: [
              BracketMatchup(teamA: championA, teamB: championB, seedA: 1, seedB: 2, status: 'projected', predictedWinner: champion, winProbability: 0.6),
            ]),
          ],
          champion: champion,
        );

    final projection = SeasonProjection(
      sport: 'ncaambb',
      season: 2027,
      standings: [],
      leaderboards: null,
      marchMadnessBracket: MarchMadnessBracket(
        firstFour: const [
          BracketMatchup(teamA: '90', teamB: '91', status: 'projected', predictedWinner: '90', winProbability: 0.55),
        ],
        regions: {
          'Region A': region('1', '2', '1', firstFourWinner: '90'),
          'Region B': region('3', '4', '3'),
          'Region C': region('5', '6', '5'),
          'Region D': region('7', '8', '7'),
        },
        finalFour: const [
          BracketMatchup(teamA: '1', teamB: '3', status: 'projected', predictedWinner: '1', winProbability: 0.52),
          BracketMatchup(teamA: '5', teamB: '7', status: 'projected', predictedWinner: '5', winProbability: 0.58),
        ],
        championship: const BracketMatchup(teamA: '1', teamB: '5', status: 'projected', predictedWinner: '1', winProbability: 0.51),
        champion: '1',
        teamNames: const {},
      ),
    );

    await pumpSeasonPage(tester, 'ncaambb', projection);
    await tester.tap(find.text('March Madness'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    // The First Four card itself is drawn inside the grid, next to the
    // Round of 64 slot its winner feeds -- only its own column header
    // reads "FIRST FOUR" (region A's side only; region B/C/D's side never
    // draws a First Four game this run).
    expect(find.text('FIRST FOUR'), findsOneWidget);
    // '90' appears twice -- once in the First Four card itself, once as
    // the Round of 64 destination slot's own teamA (see the region()
    // fixture above).
    expect(find.text('90'), findsNWidgets(2));
    for (final region in ['REGION A', 'REGION B', 'REGION C', 'REGION D']) {
      expect(find.text(region), findsOneWidget);
    }
    // The traditional grid renders 2 Final Four columns (left half's and
    // right half's, converging on the single Championship column between
    // them), not 1 -- unlike a flat round-by-round tree.
    expect(find.text('FINAL FOUR'), findsNWidgets(2));
    expect(find.text('CHAMPIONSHIP'), findsOneWidget);

    // The First Four card sits at the same row as the Round of 64 slot
    // its winner ('90') feeds -- checked via each card's own Positioned
    // (not the team-row text's rendered position), since a seeded row's
    // extra leading seed-number element shifts its own text's exact
    // baseline by a few px from an unseeded row's -- real, pre-existing,
    // and harmless (both cards' Positioned.top below prove the cards
    // themselves, not just their teamA text, are on the identical row).
    final cardPositions = tester
        .widgetList<Positioned>(find.ancestor(of: find.text('90'), matching: find.byType(Positioned)))
        .map((p) => p.top)
        .toList();
    expect(cardPositions, hasLength(2));
    expect(cardPositions[0], equals(cardPositions[1]));

    // Same distinct-vertical-slot pattern as the NBA Play-In regression
    // tests below -- '1' appears in its own region's Elite Eight card and
    // again in the Final Four card it advances to; those must not land on
    // the same row, and neither should the two region headers immediately
    // above/below each other.
    final regionAChampionTop = tester.getTopLeft(find.text('1').at(0)).dy;
    final finalFourTop = tester.getTopLeft(find.text('1').at(1)).dy;
    expect(regionAChampionTop, isNot(equals(finalFourTop)));
    final regionATop = tester.getTopLeft(find.text('REGION A')).dy;
    final regionBTop = tester.getTopLeft(find.text('REGION B')).dy;
    expect(regionATop, isNot(equals(regionBTop)));

    // The grid is far wider than any phone/desktop viewport -- a visible,
    // permanently-shown (not just on-hover) Scrollbar is the only
    // affordance a user has that the right half exists at all, since
    // desktop web doesn't click-drag a plain SingleChildScrollView.
    final scrollbar = tester.widget<Scrollbar>(find.byType(Scrollbar).last);
    expect(scrollbar.thumbVisibility, isTrue);
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
                teamA: '2', teamB: '8', seedA: 3, seedB: 6, status: 'scheduled',
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

    // The series record (2, 1) shown big per-team, not a raw box score.
    expect(find.text('2'), findsOneWidget);
    expect(find.text('1'), findsOneWidget);
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

    // The record (4-2) isn't repeated on the status line -- it's already
    // the big score digits in the team rows above.
    expect(find.textContaining('BOS WINS SERIES'), findsOneWidget);
  });

  testWidgets(
      "a not-yet-real NBA series matchup's status line shows PROJECTED + the predicted final record, "
      'without repeating the live 0-0 record the team rows already show', (tester) async {
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

    // The live 0-0 record is not repeated on the status line -- it's
    // already the big "0"/"0" score digits in the team rows.
    expect(find.text('0'), findsNWidgets(2));
    expect(find.textContaining('PROJECTED'), findsOneWidget);
    // The predicted final record (4-2, BOS the predicted winner) is the
    // one record that belongs on the status line, formatted as
    // "PROJECTED — TEAM RECORD PERCENT".
    expect(find.text('PROJECTED — BOS 4-2 70%'), findsOneWidget);

    // find.textContaining only checks the Text widget's own `data` string,
    // unaffected by paint-time ellipsis clipping -- checking the real
    // RenderParagraph is the only way to catch text actually being cut.
    final paragraph = tester.renderObject<RenderParagraph>(find.textContaining('PROJECTED'));
    expect(paragraph.didExceedMaxLines, isFalse, reason: 'status line text is being clipped, not just wrapped');
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
                teamA: '2', teamB: '8', seedA: 3, seedB: 6, status: 'scheduled',
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

    // The live 2-1 record is the big per-team score digits (not repeated
    // on the status line); the predicted 4-1 final only appears there.
    expect(find.text('2'), findsOneWidget);
    expect(find.text('1'), findsOneWidget);
    expect(find.text('BOS 4-1 81%'), findsOneWidget);
  });

  testWidgets(
      'NBA Play-In feeding Conference Quarterfinals lays out every card at a distinct '
      'vertical slot -- regression for a real overlapping-card bug', (tester) async {
    // Play-In's 3 games feed a 4-matchup Conference Quarterfinals round
    // where 2 matchups (seeds 4v5, 3v6) are byes with no traceable
    // Play-In winner.
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
    // teamNames map supplied, so teamDisplayFor falls back to the raw id)
    // should sit at a unique vertical position.
    final quarterfinalIds = ['201', '204', '203', '202'];
    final tops = {for (final id in quarterfinalIds) id: tester.getTopLeft(find.text(id)).dy};

    expect(tops['201'], isNot(equals(tops['203'])));
    expect(tops.values.toSet().length, quarterfinalIds.length);
  });

  testWidgets(
      'adjacent Quarterfinal cards stay a full card-height apart even when one lands on a '
      'fractional slot -- regression for a real visual overlap distinct-value checks missed',
      (tester) async {
    // The Play-In-split shape puts the (1v8) Quarterfinal at slot 0.5
    // (the average of Play-In Elimination's own 2-source trace) right
    // next to (4v5)'s bye fallback at slot 1.0 -- distinct slot values but
    // only half a row apart, less than one card height.
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
    // overlap, regardless of whether their slot values differ.
    final topGap = tester.getTopLeft(find.text('204')).dy - tester.getTopLeft(find.text('201')).dy;
    expect(topGap, greaterThanOrEqualTo(108));
  });

  testWidgets(
      'NBA Conference Semifinals also lay out at distinct vertical slots -- regression for a '
      'real overlapping-card bug the Quarterfinals-only fix did not cover', (tester) async {
    // Same Play-In/Quarterfinals shape as the regression test above, one
    // round further, where two different Semifinal matchups (neither a
    // bye) can average out to the same slot.
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
    // the advancing winner); the second occurrence is the Semifinal card.
    final sf0Top = tester.getTopLeft(find.text('201').at(1)).dy;
    final sf1Top = tester.getTopLeft(find.text('203').at(1)).dy;
    expect(sf0Top, isNot(equals(sf1Top)));
  });

  testWidgets(
      "one conference's Quarterfinals stay in their own (1v8)/(4v5)/(3v6)/(2v7) seed order even "
      "when the other conference's bye fallback numerically collides with it -- regression for a "
      'real cross-conference slot-stealing bug', (tester) async {
    // Both conferences use the exact same shape (byes at local round
    // positions 1 and 2). game3 first, game1 last -- matches the backend's
    // own Play-In ordering, not the raw game-number order.
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

    // Western's own Quarterfinals must read top-to-bottom in canonical
    // (1v8)/(4v5)/(3v6)/(2v7) order -- '303' (3v6) must sit above '302'
    // (2v7). '301' also appears again in the Championship card; .first
    // grabs its earlier Quarterfinals occurrence.
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
    // (208v209) is built from their results (game1's loser vs game2's
    // winner) and belongs one round later. Game 1's own winner ('207')
    // skips the Elimination round entirely and reappears two rounds later,
    // directly in the Quarterfinals' own (2v7) pairing.
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

    // Play-In Elimination must be its own column (a later round), not
    // just visually offset. Compare the two round headers' own x position.
    final playInHeaderLeft = tester.getTopLeft(find.text('PLAY-IN')).dx;
    final eliminationHeaderLeft = tester.getTopLeft(find.text('PLAY-IN ELIMINATION')).dx;
    expect(eliminationHeaderLeft, greaterThan(playInHeaderLeft));

    // Quarterfinals must read top-to-bottom in seed order -- '208' (fed by
    // the Elimination Game, one round back) and '207' (skips the
    // Elimination round, sourced two rounds back) both drive real
    // Quarterfinal positions; the two byes sit between them, matching
    // (1v8)/(4v5)/(3v6)/(2v7).
    final quarterfinalIds = ['201', '204', '203', '202'];
    final tops = {for (final id in quarterfinalIds) id: tester.getTopLeft(find.text(id).last).dy};
    expect(tops['201'], lessThan(tops['204']!));
    expect(tops['204'], lessThan(tops['203']!));
    expect(tops['203'], lessThan(tops['202']!));

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
