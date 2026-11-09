# The app client used by the frontend SDK. No client secret -- any client
# that can't safely store a secret (browser, mobile, desktop) uses a public
# client.
#
# ALLOW_USER_SRP_AUTH: sign-in is a Secure Remote Password exchange
# (Source/front-end/lib/core/auth/cognito_srp.dart), so the password itself
# is never sent to Cognito -- only a proof derived from it.
#
# prevent_user_existence_errors ensures both valid and invalid usernames
# return the same error, preventing username enumeration.
resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.project}-web-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  supported_identity_providers = ["COGNITO"]

  prevent_user_existence_errors = "ENABLED"

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

# The Android app's client: same SRP sign-in as `web`, plus refresh-token
# rotation (GetTokensFromRefreshToken -- the only refresh path once rotation
# is on; Cognito rejects ALLOW_REFRESH_TOKEN_AUTH on a rotating client).
# A rotated refresh token keeps the first sign-in's expiry, so Cognito can't
# end a session on inactivity: the app signs out after 30 days without being
# opened (appInactivityTtl in the app), and this client's refresh token is a
# long outer limit (var.mobile_refresh_token_days). The 60s grace period
# covers the app and its background job rotating the same token at once.
# Rotation needs the Essentials or Plus feature plan.
resource "aws_cognito_user_pool_client" "mobile" {
  name         = "${var.project}-mobile-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH"]

  supported_identity_providers = ["COGNITO"]

  prevent_user_existence_errors = "ENABLED"

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = var.mobile_refresh_token_days

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 60
  }
}
