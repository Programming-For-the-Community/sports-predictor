import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/features/auth/login_page.dart';

import '../../support/cognito_srp_test_support.dart';

Future<void> _pumpLoginPage(WidgetTester tester, {required http.Client httpClient}) async {
  SharedPreferences.setMockInitialValues({});
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        authRepositoryProvider.overrideWith(
          (ref) => AuthRepository(authClient: CognitoAuthClient(srpFactory: FakeCognitoSrp.new, httpClient: httpClient)),
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
      httpClient: srpAwareMockClient((request) async {
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
      httpClient: srpAwareMockClient((request) async {
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

  testWidgets("shows Cognito's own message for any other error, submitting from the password field", (tester) async {
    await _pumpLoginPage(
      tester,
      httpClient: srpAwareMockClient((request) async => http.Response(
            jsonEncode({'__type': 'TooManyRequestsException', 'message': 'Slow down'}),
            400,
          )),
    );

    await tester.enterText(find.widgetWithText(TextField, 'Username'), 'chamar');
    await tester.enterText(find.widgetWithText(TextField, 'Password'), 'pw');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    expect(find.text('Slow down'), findsOneWidget);
  });

  testWidgets('submits a new password from the keyboard, showing a spinner while in flight', (tester) async {
    final pending = Completer<http.Response>();
    var calls = 0;
    await _pumpLoginPage(
      tester,
      httpClient: srpAwareMockClient((request) async {
        calls++;
        if (calls == 1) {
          return http.Response(jsonEncode({'ChallengeName': 'NEW_PASSWORD_REQUIRED', 'Session': 'sess-1'}), 200);
        }
        return pending.future;
      }),
    );
    await tester.enterText(find.widgetWithText(TextField, 'Username'), 'chamar');
    await tester.enterText(find.widgetWithText(TextField, 'Password'), 'temp');
    await tester.tap(find.widgetWithText(ElevatedButton, 'Sign in'));
    await tester.pumpAndSettle();

    await tester.enterText(find.widgetWithText(TextField, 'New password'), 'new-pw');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump();

    expect(calls, 2);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    pending.complete(http.Response(jsonEncode({'__type': 'InvalidPasswordException', 'message': 'Too short'}), 400));
    await tester.pumpAndSettle();
    expect(find.text('Too short'), findsOneWidget);
  });
}
