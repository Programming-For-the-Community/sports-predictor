import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event.dart';

void main() {
  test('parses home/away participants from an event', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2025-09-28',
      'status': 'scheduled',
      'week': 4,
      'participants': [
        {'entity_id': 'KC', 'role': 'home'},
        {'entity_id': 'LAC', 'role': 'away'},
      ],
    });

    expect(event.eventId, '401547417');
    expect(event.home.entityId, 'KC');
    expect(event.away.entityId, 'LAC');
    expect(event.home.result, isNull);
    expect(event.predictionComparison, isNull);
    expect(event.round, isNull);
  });

  test('parses a playoff round label for a postseason event', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2026-01-11',
      'status': 'scheduled',
      'week': 1,
      'round': 'Wild Card',
      'participants': [
        {'entity_id': 'KC', 'role': 'home'},
        {'entity_id': 'LAC', 'role': 'away'},
      ],
    });

    expect(event.round, 'Wild Card');
  });

  test('parses final scores and a prediction comparison for a completed event', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2025-09-28',
      'status': 'completed',
      'week': 4,
      'participants': [
        {
          'entity_id': 'KC', 'role': 'home',
          'result': {'score': 24, 'won': true},
        },
        {
          'entity_id': 'LAC', 'role': 'away',
          'result': {'score': 17, 'won': false},
        },
      ],
      'prediction_comparison': {
        'predicted_home_win_probability': 0.71,
        'predicted_home_won': true,
        'actual_home_won': true,
        'correct': true,
        'predicted_margin': 6.2,
        'actual_margin': 7,
        'predicted_home_score': 27.4,
        'predicted_away_score': 21.2,
        'actual_home_score': 24,
        'actual_away_score': 17,
      },
    });

    expect(event.home.result!.score, 24);
    expect(event.home.result!.won, isTrue);
    expect(event.predictionComparison!.correct, isTrue);
    expect(event.predictionComparison!.actualMargin, 7);
  });

  test('prediction_comparison is null when the backend never logged one', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2025-09-28',
      'status': 'completed',
      'week': 4,
      'participants': [
        {
          'entity_id': 'KC', 'role': 'home',
          'result': {'score': 24, 'won': true},
        },
        {
          'entity_id': 'LAC', 'role': 'away',
          'result': {'score': 17, 'won': false},
        },
      ],
      'prediction_comparison': null,
    });

    expect(event.predictionComparison, isNull);
  });

  test('parses leaders_comparison predicted-vs-actual for a completed event', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2025-09-28',
      'status': 'completed',
      'week': 4,
      'participants': [
        {
          'entity_id': 'KC', 'role': 'home',
          'result': {'score': 24, 'won': true},
        },
        {
          'entity_id': 'LAC', 'role': 'away',
          'result': {'score': 17, 'won': false},
        },
      ],
      'leaders_comparison': {
        'home': {
          'passing': {
            'entity_id': 'qb1', 'name': 'Patrick Mahomes',
            'predicted': {'passing_yards': 267.0},
            'actual': {'passing_yards': 289.0},
          },
          'receiving': [],
          'rushing': [],
          'sacks': [],
        },
        'away': {'passing': null, 'receiving': [], 'rushing': [], 'sacks': []},
      },
    });

    final passing = event.leadersComparison!.home.passing!;
    expect(passing.displayName, 'Patrick Mahomes');
    expect(passing.predicted['passing_yards'], 267.0);
    expect(passing.actual['passing_yards'], 289.0);
    expect(event.leadersComparison!.away.passing, isNull);
  });

  test('leaders_comparison is null when nobody recorded a prediction before the game', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2025-09-28',
      'status': 'completed',
      'week': 4,
      'participants': [
        {
          'entity_id': 'KC', 'role': 'home',
          'result': {'score': 24, 'won': true},
        },
        {
          'entity_id': 'LAC', 'role': 'away',
          'result': {'score': 17, 'won': false},
        },
      ],
      'leaders_comparison': null,
    });

    expect(event.leadersComparison, isNull);
  });
}
