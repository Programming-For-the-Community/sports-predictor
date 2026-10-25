import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/upset.dart';

void main() {
  group('upsetFlag before the game is final', () {
    test('the model giving the betting favourite 60% is an upset watch', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: false, homeWinProbability: 0.60), UpsetFlag.watch);
    });

    test('61% for the betting favourite is not', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: false, homeWinProbability: 0.61), isNull);
    });

    test('compares the percent the row shows, so 60.4% still counts', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: false, homeWinProbability: 0.604), UpsetFlag.watch);
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: false, homeWinProbability: 0.605), isNull);
    });

    test('the model backing the underdog is an upset watch', () {
      expect(upsetFlag(favorite: 'away', favoriteMoneyline: -220, isFinal: false, homeWinProbability: 0.7), UpsetFlag.watch);
    });

    test('needs a prediction', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: false), isNull);
    });
  });

  group('upsetFlag once final', () {
    test('the betting favourite losing is an upset, whatever the model said', () {
      expect(
        upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: true, homeWon: false, homeWinProbability: 0.9),
        UpsetFlag.upset,
      );
    });

    test('the betting favourite winning is nothing, however close', () {
      expect(
        upsetFlag(favorite: 'away', favoriteMoneyline: -220, isFinal: true, homeWon: false, homeWinProbability: 0.55),
        isNull,
      );
    });

    test('a tie is nothing', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -220, isFinal: true), isNull);
    });
  });

  group('upsetFlag by the favourite\'s moneyline', () {
    test('-200 counts', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -200, isFinal: true, homeWon: false), UpsetFlag.upset);
      expect(upsetFlag(favorite: 'away', favoriteMoneyline: -200, isFinal: false, homeWinProbability: 0.7), UpsetFlag.watch);
    });

    test('-199 is too close to count', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: -199, isFinal: true, homeWon: false), isNull);
      expect(upsetFlag(favorite: 'away', favoriteMoneyline: -199, isFinal: false, homeWinProbability: 0.7), isNull);
    });

    test('no moneyline, no flag', () {
      expect(upsetFlag(favorite: 'home', favoriteMoneyline: null, isFinal: true, homeWon: false), isNull);
    });
  });

  test('no line, no flag', () {
    expect(upsetFlag(favorite: null, favoriteMoneyline: null, isFinal: false, homeWinProbability: 0.5), isNull);
    expect(upsetFlag(favorite: null, favoriteMoneyline: null, isFinal: true, homeWon: true), isNull);
  });
}
