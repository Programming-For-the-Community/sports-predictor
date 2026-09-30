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
