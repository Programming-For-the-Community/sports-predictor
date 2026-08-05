import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../auth/auth_repository.dart';
import '../config/app_config.dart';
import 'api_exception.dart';

/// Thin GET-only wrapper (every route this app calls today is a GET) around
/// the nfl-predict API. Sends the raw ID token as the Authorization header
/// value -- no "Bearer " prefix. API Gateway's COGNITO_USER_POOLS
/// authorizer expects an ID token specifically when a method has no
/// authorization_scopes configured (true of every route here); it only
/// accepts an access token when scopes ARE configured. See
/// AuthRepository.getValidIdToken's own doc comment -- confirmed live
/// against the real deployed API after the access token was rejected with
/// a 401 regardless of freshness.
class ApiClient {
  ApiClient(this._ref, {http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  // 30s, not 15s -- API Gateway's integration timeout for every route here
  // is a hard 29s (see Terraform/lambda-nfl-predict.tf), so a 15s client
  // timeout was giving up before the backend's own authoritative response
  // (success OR its 504) could ever arrive, most visibly on /nfl/season
  // (its multi-model leaderboard computation routinely ran 26-29s). 30s
  // gives just enough margin to actually observe that 504 instead of
  // masking it with a misleading client-side "timed out" message.
  static const _timeout = Duration(seconds: 30);

  final Ref _ref;
  final http.Client _httpClient;

  Future<dynamic> get(String path, {Map<String, String>? queryParameters}) async {
    final uri = Uri.parse('${AppConfig.apiBaseUrl}$path').replace(queryParameters: queryParameters);
    final stopwatch = Stopwatch()..start();
    debugPrint('[ApiClient] GET $uri -- requesting id token');
    final response = await _send(uri, await _authHeader());

    if (response.statusCode == 401) {
      // One reactive retry, covering clock skew between our proactive
      // refresh and the server's actual expiry check.
      debugPrint('[ApiClient] GET $uri -- got 401, retrying with a forced token refresh');
      final retried = await _send(uri, await _authHeader(forceRefresh: true));
      final decoded = _decode(retried);
      debugPrint('[ApiClient] GET $uri -- done (after retry) in ${stopwatch.elapsedMilliseconds}ms');
      return decoded;
    }
    final decoded = _decode(response);
    debugPrint('[ApiClient] GET $uri -- done in ${stopwatch.elapsedMilliseconds}ms');
    return decoded;
  }

  Future<http.Response> _send(Uri uri, Map<String, String> headers) async {
    // Without this, a stalled connection (DNS not resolved, a CloudFront
    // distribution still propagating, a hung TCP handshake) never
    // resolves OR rejects the Future -- the UI just spins forever instead
    // of ever reaching an error state. A CORS rejection specifically
    // still fails fast on its own and isn't what this guards against.
    final stopwatch = Stopwatch()..start();
    debugPrint('[ApiClient] -> sending GET $uri');
    try {
      final response = await _httpClient.get(uri, headers: headers).timeout(
        _timeout,
        onTimeout: () => throw ApiException(0, 'Request to $uri timed out after ${_timeout.inSeconds}s -- '
            'check the API is reachable and, if calling from a local dev server, that CORS is configured'),
      );
      debugPrint('[ApiClient] <- $uri responded ${response.statusCode} in ${stopwatch.elapsedMilliseconds}ms '
          '(${response.bodyBytes.length} bytes)');
      return response;
    } catch (error) {
      debugPrint('[ApiClient] <- $uri FAILED after ${stopwatch.elapsedMilliseconds}ms: $error');
      rethrow;
    }
  }

  Future<Map<String, String>> _authHeader({bool forceRefresh = false}) async {
    final stopwatch = Stopwatch()..start();
    debugPrint('[ApiClient] -- resolving id token (forceRefresh=$forceRefresh)');
    final authRepository = _ref.read(authRepositoryProvider.notifier);
    try {
      final token = await authRepository.getValidIdToken(forceRefresh: forceRefresh);
      debugPrint('[ApiClient] -- got id token in ${stopwatch.elapsedMilliseconds}ms');
      return {'Authorization': token};
    } catch (error) {
      debugPrint('[ApiClient] -- failed to resolve id token after ${stopwatch.elapsedMilliseconds}ms: $error');
      rethrow;
    }
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
