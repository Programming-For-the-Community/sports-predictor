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

# The Android app's client: same SRP sign-in and lifetimes as `web`, plus
# refresh-token rotation. The app rotates on every launch and resume
# (GetTokensFromRefreshToken -- the only refresh path once rotation is on;
# Cognito rejects ALLOW_REFRESH_TOKEN_AUTH on a rotating client), and each
# rotation issues a refresh token with a fresh 30-day validity -- a rolling
# session that only ends after 30 days away. The 60s grace period covers
# the app and its background job rotating the same token at once. Rotation
# needs the Essentials or Plus feature plan.
resource "aws_cognito_user_pool_client" "mobile" {
  name         = "${var.project}-mobile-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH"]

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

  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 60
  }
}
