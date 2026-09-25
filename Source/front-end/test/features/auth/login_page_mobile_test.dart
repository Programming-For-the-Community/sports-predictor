import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';
import 'package:front_end/features/auth/login_page.dart';

import '../../support/mobile_viewport.dart';

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow or clipped text at ${width}px wide', (tester) async {
      SharedPreferences.setMockInitialValues({});
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            authRepositoryProvider.overrideWith(
              (ref) => AuthRepository(authClient: CognitoAuthClient(httpClient: MockClient((request) async => throw UnimplementedError()))),
            ),
          ],
          child: const MaterialApp(home: LoginPage()),
        ),
      );
      expect(tester.takeException(), isNull);
    });
  }
}
