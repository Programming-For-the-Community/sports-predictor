import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../config/app_config.dart';

/// The one published APK: a single S3 object at a stable key
/// (Terraform/s3-mobile-releases.tf), served through CloudFront's /app/*
/// behavior. mobile_sync_deploy.yml overwrites it on every release; the
/// bucket's versioning keeps the previous builds.
const appReleasePath = '/app/sports-predictor.apk';

String get appReleaseUrl => '${AppConfig.apiBaseUrl}$appReleasePath';

/// CloudFront serves /app/* only to Android user agents
/// (Terraform/cloudfront.tf's android_only function). Android browsers
/// already send one; the app's own Dart and download clients send this.
const androidAppUserAgent = 'SportsPredictor (Linux; Android)';

/// The published APK's S3 user metadata, as mobile_sync_deploy.yml writes it.
class AppRelease {
  const AppRelease({required this.versionCode, required this.versionName, required this.sha256, this.sizeBytes, this.notes});

  final int versionCode;
  final String versionName;
  final String sha256;
  final int? sizeBytes;
  final String? notes;

  String get label => 'v$versionName ($versionCode)';

  /// Null unless the response carries the release metadata -- a missing
  /// object comes back as CloudFront's 403 page, not the APK.
  static AppRelease? fromHeaders(Map<String, String> headers) {
    final versionCode = int.tryParse(headers['x-amz-meta-version-code'] ?? '');
    final versionName = headers['x-amz-meta-version-name'];
    final sha256 = headers['x-amz-meta-sha256'];
    if (versionCode == null || versionName == null || sha256 == null) return null;
    final notes = headers['x-amz-meta-notes'];
    return AppRelease(
      versionCode: versionCode,
      versionName: versionName,
      sha256: sha256,
      sizeBytes: int.tryParse(headers['content-length'] ?? ''),
      notes: (notes == null || notes.isEmpty) ? null : notes,
    );
  }
}

/// "17.8 MB"
String formatMegabytes(int bytes) => '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';

class AppReleaseClient {
  AppReleaseClient({http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  // Browsers forbid setting User-Agent; theirs already names Android.
  static const _headers = kIsWeb ? <String, String>{} : {'User-Agent': androidAppUserAgent};

  static const _timeout = Duration(seconds: 15);

  final http.Client _httpClient;

  /// A HEAD request -- only the object's metadata, never the APK bytes.
  /// Null when nothing is published or the request fails.
  Future<AppRelease?> fetchLatest() async {
    try {
      final response = await _httpClient.head(Uri.parse(appReleaseUrl), headers: _headers).timeout(_timeout);
      if (response.statusCode != 200) return null;
      return AppRelease.fromHeaders(response.headers);
    } catch (error) {
      debugPrint('[AppReleaseClient] HEAD $appReleaseUrl failed: $error');
      return null;
    }
  }
}
