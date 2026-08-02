import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/features/auth/login_page.dart';

Future<void> _pumpLoginPage(WidgetTester tester, {required http.Client httpClient}) async {
  SharedPreferences.setMockInitialValues({});
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        authRepositoryProvider.overrideWith(
          (ref) => AuthRepository(authClient: CognitoAuthClient(httpClient: httpClient)),
        ),
      ],
      child: const MaterialApp(home: LoginPage()),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows an error message on incorrect credentials', (tester) async {
    await _pumpLoginPage(
      tester,
      httpClient: MockClient((request) async {
        return http.Response(
          jsonEncode({'__type': 'NotAuthorizedException', 'message': 'bad creds'}),
          400,
        );
      }),
    );

    await tester.enterText(find.widgetWithText(TextField, 'Username'), 'chamar');
    await tester.enterText(find.widgetWithText(TextField, 'Password'), 'wrong');
    await tester.tap(find.widgetWithText(ElevatedButton, 'Sign in'));
    await tester.pumpAndSettle();

    expect(find.text('Incorrect username or password.'), findsOneWidget);
  });

  testWidgets('switches to the new-password form on a NEW_PASSWORD_REQUIRED challenge', (tester) async {
    await _pumpLoginPage(
      tester,
      httpClient: MockClient((request) async {
        return http.Response(
          jsonEncode({'ChallengeName': 'NEW_PASSWORD_REQUIRED', 'Session': 'sess-1'}),
          200,
        );
      }),
    );

    await tester.enterText(find.widgetWithText(TextField, 'Username'), 'chamar');
    await tester.enterText(find.widgetWithText(TextField, 'Password'), 'temp-pass');
    await tester.tap(find.widgetWithText(ElevatedButton, 'Sign in'));
    await tester.pumpAndSettle();

    expect(find.text('Set a new password'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'New password'), findsOneWidget);
  });
}
