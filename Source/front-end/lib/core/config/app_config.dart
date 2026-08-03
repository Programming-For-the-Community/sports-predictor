/// Flutter Web has no server runtime to read environment variables from at
/// request time -- everything here resolves at COMPILE time via
/// --dart-define-from-file. Local dev uses config/dev.json (checked in --
/// these are all public values, no client secret exists on the Cognito app
/// client). CI generates config/prod.json from live Terraform outputs via
/// scripts/render_frontend_config.sh immediately before `flutter build web`
/// -- see config/prod.json.example for the shape it must match.
class AppConfig {
  AppConfig._();

  static const apiBaseUrl = String.fromEnvironment('API_BASE_URL');
  static const cognitoUserPoolId = String.fromEnvironment('COGNITO_USER_POOL_ID');
  static const cognitoClientId = String.fromEnvironment('COGNITO_CLIENT_ID');
  static const awsRegion = String.fromEnvironment('AWS_REGION', defaultValue: 'us-east-2');
}
