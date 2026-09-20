import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/app.dart';
import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';

class _RecordingAuthRepository extends AuthRepository {
  _RecordingAuthRepository()
      : super(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));

  int recordActivityCalls = 0;

  @override
  Future<void> recordActivity() async {
    recordActivityCalls++;
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('renders the app shell with its title and dark theme', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: App()));
    await tester.pump();

    final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.title, 'sports-predictor');
    expect(app.debugShowCheckedModeBanner, isFalse);
    expect(app.theme?.brightness, Brightness.dark);
  });

  testWidgets('records activity on a pointer-down interaction', (tester) async {
    final authRepo = _RecordingAuthRepository();
    await tester.pumpWidget(ProviderScope(
      overrides: [authRepositoryProvider.overrideWith((ref) => authRepo)],
      child: const App(),
    ));
    await tester.pump();

    final listener = tester.widget<Listener>(find.byType(Listener).first);
    listener.onPointerDown!(const PointerDownEvent());

    expect(authRepo.recordActivityCalls, 1);
  });

  testWidgets('records activity on a pointer-signal (scroll) interaction', (tester) async {
    final authRepo = _RecordingAuthRepository();
    await tester.pumpWidget(ProviderScope(
      overrides: [authRepositoryProvider.overrideWith((ref) => authRepo)],
      child: const App(),
    ));
    await tester.pump();

    final listener = tester.widget<Listener>(find.byType(Listener).first);
    listener.onPointerSignal!(const PointerScrollEvent());

    expect(authRepo.recordActivityCalls, 1);
  });
}
