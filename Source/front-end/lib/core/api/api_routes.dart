/// Backend API endpoint path templates -- every repository under
/// core/data/ hits one of these route shapes (see
/// core/models/sport_config.dart's own doc comment: "every sport hits the
/// same `/{sport}/events`, `/{sport}/predictions/...`, `/{sport}/models`
/// route shapes"). The one place a resource path is ever spelled out as a
/// string; every repository calls one of these instead of retyping the
/// template, same "single source of truth" reasoning SportIds already
/// applies to the sport-id segment these embed.
abstract final class ApiRoutes {
  static String events(String sport) => '/$sport/events';
  static String eventPrediction(String sport, String eventId) => '/$sport/predictions/events/$eventId';
  static String liveScores(String sport) => '/$sport/live-scores';
  static String models(String sport) => '/$sport/models';
  static String modelPerformance(String sport) => '/$sport/model-performance';
  static String season(String sport) => '/$sport/season';
}
