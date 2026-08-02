import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../auth/auth_repository.dart';
import '../config/app_config.dart';
import 'api_exception.dart';

/// Thin GET-only wrapper (every route this app calls today is a GET) around
/// the nfl-predict API. Sends the raw access token as the Authorization
/// header value -- no "Bearer " prefix, matching the API Gateway
/// COGNITO_USER_POOLS authorizer's expectation (confirmed against the real
/// deployed API during backend development).
class ApiClient {
  ApiClient(this._ref, {http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  final Ref _ref;
  final http.Client _httpClient;

  Future<dynamic> get(String path, {Map<String, String>? queryParameters}) async {
    final uri = Uri.parse('${AppConfig.apiBaseUrl}$path').replace(queryParameters: queryParameters);
    final response = await _send(uri, await _authHeader());

    if (response.statusCode == 401) {
      // One reactive retry, covering clock skew between our proactive
      // refresh and the server's actual expiry check.
      final retried = await _send(uri, await _authHeader(forceRefresh: true));
      return _decode(retried);
    }
    return _decode(response);
  }

  Future<http.Response> _send(Uri uri, Map<String, String> headers) => _httpClient.get(uri, headers: headers);

  Future<Map<String, String>> _authHeader({bool forceRefresh = false}) async {
    final authRepository = _ref.read(authRepositoryProvider.notifier);
    final token = await authRepository.getValidAccessToken();
    return {'Authorization': token};
  }

  dynamic _decode(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      String message = response.body;
      try {
        final decoded = jsonDecode(response.body);
        if (decoded is Map && decoded['error'] != null) {
          message = decoded['error'] as String;
        }
      } catch (_) {
        // Body wasn't JSON -- fall back to the raw text already assigned.
      }
      throw ApiException(response.statusCode, message);
    }
    if (response.body.isEmpty) return null;
    return jsonDecode(response.body);
  }
}

final apiClientProvider = Provider<ApiClient>((ref) => ApiClient(ref));
