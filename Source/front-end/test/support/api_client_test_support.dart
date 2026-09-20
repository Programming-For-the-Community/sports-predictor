import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/core/api/api_client.dart';
import 'package:front_end/core/auth/auth_repository.dart';
import 'package:front_end/core/auth/cognito_auth_client.dart';

class _FixedTokenAuthRepository extends AuthRepository {
  _FixedTokenAuthRepository()
      : super(authClient: CognitoAuthClient(httpClient: MockClient((r) async => http.Response('{}', 200))));

  @override
  Future<String> getValidIdToken({bool forceRefresh = false}) async => 'test-token';
}

/// Builds a real ApiClient backed by `handler` instead of a real network
/// call -- every core/data/*_repository.dart test uses this instead of
/// repeating the ProviderContainer/fake-auth boilerplate api_client_test.dart
/// itself establishes.
ApiClient buildTestApiClient(Future<http.Response> Function(http.Request) handler) {
  SharedPreferences.setMockInitialValues({});
  final container = ProviderContainer(overrides: [
    authRepositoryProvider.overrideWith((ref) => _FixedTokenAuthRepository()),
  ]);
  final refProvider = Provider<Ref>((ref) => ref);
  final ref = container.read(refProvider);
  return ApiClient(ref, httpClient: MockClient(handler));
}
