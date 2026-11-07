import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../core/auth/auth_repository.dart';
import '../../core/config/app_config.dart';
import 'assistant_models.dart';

/// Talks to the two AgentCore runtimes through CloudFront
/// (Terraform/cloudfront.tf's /assistant/* route). Each request carries the
/// Cognito ID token -- the runtime's own authorizer checks it -- and the
/// conversation's session id. The answer streams back as SSE `data:` lines;
/// on web the browser client delivers them together at the end.
class AssistantClient {
  AssistantClient(this._ref, {http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  static const sessionHeader = 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id';
  // A turn can make several model and tool calls; CloudFront allows 60s
  // between streamed events.
  static const _timeout = Duration(seconds: 90);

  final Ref _ref;
  final http.Client _httpClient;

  Stream<AssistantEvent> send(AssistantMode mode, String sessionId, Map<String, Object?> body) async* {
    var response = await _post(mode, sessionId, body, forceRefresh: false);
    if (response.statusCode == 401) {
      await response.stream.drain<void>();
      response = await _post(mode, sessionId, body, forceRefresh: true);
    }
    if (response.statusCode != 200) {
      await response.stream.drain<void>();
      yield _failureFor(response.statusCode);
      return;
    }
    final lines = response.stream.transform(utf8.decoder).transform(const LineSplitter()).timeout(_timeout);
    await for (final line in lines) {
      if (!line.startsWith('data:')) continue;
      final decoded = jsonDecode(line.substring(5).trim());
      if (decoded is Map<String, dynamic>) yield AssistantEvent.fromJson(decoded);
    }
  }

  Future<http.StreamedResponse> _post(AssistantMode mode, String sessionId, Map<String, Object?> body, {required bool forceRefresh}) async {
    final token = await _ref.read(authRepositoryProvider.notifier).getValidIdToken(forceRefresh: forceRefresh);
    final request = http.Request('POST', Uri.parse('${AppConfig.apiBaseUrl}${mode.path}'))
      ..headers.addAll({
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        sessionHeader: sessionId,
      })
      ..body = jsonEncode(body);
    return _httpClient.send(request).timeout(_timeout);
  }

  static AssistantFailure _failureFor(int status) => switch (status) {
        403 => const AssistantFailure('forbidden', 'The assistant isn\'t available to this account.'),
        429 => const AssistantFailure('rate_limited', 'Too many questions too quickly. Wait a minute and try again.'),
        _ => const AssistantFailure('failed', 'The assistant couldn\'t be reached. Please try again.'),
      };
}

final assistantClientProvider = Provider<AssistantClient>((ref) => AssistantClient(ref));
