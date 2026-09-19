/// In-app navigation path templates -- app_router.dart's own `GoRoute`
/// definitions and every `context.go(...)` call site reference these
/// instead of retyping the template, so a path shape can only ever change
/// in one place. Same `abstract final class` shape as ApiRoutes/SportIds.
abstract final class AppRoutes {
  static const login = '/login';
  static const splash = '/splash';
  static const home = '/';
  static String events(String sport) => '/$sport/events';
  static String eventDetail(String sport, String eventId) => '/$sport/events/$eventId';
  static String models(String sport) => '/$sport/models';
  static String season(String sport) => '/$sport/season';
}
