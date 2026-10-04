import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

enum NotificationChannel {
  modelReports('model_reports', 'Weekly model reports', 'How each sport\'s models did over the last week or event'),
  appUpdates('app_updates', 'App updates', 'A new version of the app is ready to install');

  const NotificationChannel(this.id, this.title, this.description);

  final String id;
  final String title;
  final String description;
}

abstract interface class LocalNotifier {
  /// [route] is the in-app location a tap opens (an AppRoutes path).
  Future<void> show({required int id, required NotificationChannel channel, required String title, required String body, required String route});
}

/// Notifications are raised on the device itself -- by the app, or by the
/// background WorkManager job -- with no push service involved.
class DeviceNotifier implements LocalNotifier {
  DeviceNotifier([FlutterLocalNotificationsPlugin? plugin]) : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  final FlutterLocalNotificationsPlugin _plugin;
  bool _initialized = false;

  static const _initSettings = InitializationSettings(android: AndroidInitializationSettings('ic_notification'));

  /// [onTap] receives the tapped notification's route while the app is
  /// running; [launchRoute] covers a tap that cold-started the app.
  Future<void> initialize({void Function(String route)? onTap}) async {
    _initialized = true;
    await _plugin.initialize(
      _initSettings,
      onDidReceiveNotificationResponse: (response) {
        final route = response.payload;
        if (route != null && onTap != null) onTap(route);
      },
    );
  }

  Future<String?> launchRoute() async {
    final details = await _plugin.getNotificationAppLaunchDetails();
    return (details?.didNotificationLaunchApp ?? false) ? details?.notificationResponse?.payload : null;
  }

  /// Android 13+ asks once; earlier versions grant at install.
  Future<void> requestPermission() async {
    await _plugin.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()?.requestNotificationsPermission();
  }

  @override
  Future<void> show({required int id, required NotificationChannel channel, required String title, required String body, required String route}) async {
    if (!_initialized) await initialize();
    await _plugin.show(
      id,
      title,
      body,
      NotificationDetails(
        android: AndroidNotificationDetails(
          channel.id,
          channel.title,
          channelDescription: channel.description,
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
      payload: route,
    );
  }
}

final deviceNotifierProvider = Provider<DeviceNotifier>((ref) => DeviceNotifier());
