import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:front_end/core/mobile/app_release.dart';

const releaseHeaders = {
  'x-amz-meta-version-code': '47',
  'x-amz-meta-version-name': '1.1.0',
  'x-amz-meta-sha256': 'abc123',
  'x-amz-meta-notes': 'Faster startup',
  'content-length': '18979225',
};

void main() {
  group('AppRelease.fromHeaders', () {
    test('reads the release metadata mobile_sync_deploy.yml writes', () {
      final release = AppRelease.fromHeaders(releaseHeaders)!;

      expect(release.versionCode, 47);
      expect(release.versionName, '1.1.0');
      expect(release.sha256, 'abc123');
      expect(release.sizeBytes, 18979225);
      expect(release.notes, 'Faster startup');
      expect(release.label, 'v1.1.0 (47)');
    });

    test('is null without the version metadata', () {
      expect(AppRelease.fromHeaders({'content-type': 'text/html'}), isNull);
      expect(AppRelease.fromHeaders({...releaseHeaders, 'x-amz-meta-version-code': 'x'}), isNull);
    });

    test('treats blank notes as none', () {
      expect(AppRelease.fromHeaders({...releaseHeaders, 'x-amz-meta-notes': ''})!.notes, isNull);
    });
  });

  group('AppReleaseClient.fetchLatest', () {
    test('sends a HEAD for the stable APK key', () async {
      late http.BaseRequest seen;
      final client = AppReleaseClient(httpClient: MockClient((request) async {
        seen = request;
        return http.Response('', 200, headers: releaseHeaders);
      }));

      final release = await client.fetchLatest();

      expect(seen.method, 'HEAD');
      expect(seen.headers['User-Agent'], contains('Android'));
      expect(seen.url.path, appReleasePath);
      expect(release?.versionCode, 47);
    });

    test('is null when nothing is published (CloudFront\'s 403 page)', () async {
      final client = AppReleaseClient(httpClient: MockClient((request) async => http.Response('', 403)));

      expect(await client.fetchLatest(), isNull);
    });

    test('is null when the request fails', () async {
      final client = AppReleaseClient(httpClient: MockClient((request) async => throw http.ClientException('offline')));

      expect(await client.fetchLatest(), isNull);
    });
  });

  test('formatMegabytes', () {
    expect(formatMegabytes(18979225), '18.1 MB');
  });
}
