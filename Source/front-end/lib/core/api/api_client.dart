import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../auth/auth_repository.dart';
import '../config/app_config.dart';
import 'api_exception.dart';

/// Thin GET-only wrapper (every route this app calls today is a GET) around
/// the nfl-predict API. Sends the raw ID token as the Authorization header
/// value -- no "Bearer " prefix. API Gateway's COGNITO_USER_POOLS
/// authorizer expects an ID token when a method has no authorization_scopes
/// configured (true of every route here).
class ApiClient {
  ApiClient(this._ref, {http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  // API Gateway's integration timeout for every route here is a hard 29s,
  // so 30s gives just enough margin to observe that timeout's own 504
  // instead of masking it with a client-side "timed out" message first.
  static const _timeout = Duration(seconds: 30);

  final Ref _ref;
  final http.Client _httpClient;
  final Random _random = Random();

  // API Gateway's stage-wide throttle is one shared budget across every
  // route and every sport -- a single event list page load fans out one
  // request per visible event, so a burst of 429s is expected here.
  // Retried with exponential backoff + full jitter so that when many rows
  // 429 in the same tick, their retries land spread out instead of
  // re-forming the same burst.
  static const _maxRetries429 = 4;
  static const _baseBackoff = Duration(milliseconds: 250);

  Future<dynamic> get(String path, {Map<String, String>? queryParameters}) async {
    final uri = Uri.parse('${AppConfig.apiBaseUrl}$path').replace(queryParameters: queryParameters);
    final stopwatch = Stopwatch()..start();
    debugPrint('[ApiClient] GET $uri -- requesting id token');
    var response = await _send(uri, await _authHeader());

    if (response.statusCode == 401) {
      // One reactive retry, covering clock skew between our proactive
      // refresh and the server's actual expiry check.
      debugPrint('[ApiClient] GET $uri -- got 401, retrying with a forced token refresh');
      response = await _send(uri, await _authHeader(forceRefresh: true));
    }

    for (var attempt = 0; response.statusCode == 429 && attempt < _maxRetries429; attempt++) {
      final backoff = _baseBackoff * pow(2, attempt).toInt();
      final jittered = Duration(milliseconds: _random.nextInt(backoff.inMilliseconds + 1));
      debugPrint('[ApiClient] GET $uri -- got 429, retrying in ${jittered.inMilliseconds}ms (attempt ${attempt + 1}/$_maxRetries429)');
      await Future.delayed(jittered);
      response = await _send(uri, await _authHeader());
    }

    final decoded = _decode(response);
    debugPrint('[ApiClient] GET $uri -- done in ${stopwatch.elapsedMilliseconds}ms');
    return decoded;
  }

  Future<http.Response> _send(Uri uri, Map<String, String> headers) async {
    // Without the .timeout below, a stalled connection never resolves or
    // rejects the Future -- the UI would spin forever instead of reaching
    // an error state.
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
