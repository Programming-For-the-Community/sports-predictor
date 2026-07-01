# One pool for all app users. Self-signup is disabled -- every user is
# created by the admin (aws cognito-idp admin-create-user --username <name>
# --temporary-password <temp> ...). The admin shares the temporary password
# with the user out of band; the user sets their own permanent password on
# first login. Users can change their own password at any time via the
# ChangePassword API once they hold a valid access token.
#
# Username is the only identifier -- no email attribute. Password resets
# go through the admin:
#   aws cognito-idp admin-set-user-password --user-pool-id <id> \
#     --username <name> --password <new> --permanent
resource "aws_cognito_user_pool" "main" {
  name = "${var.project}-users"

  deletion_protection = "ACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_configuration {
    case_sensitive = true
  }

  # Admin is the only recovery path -- no email attribute to send a reset
  # link to, and no self-service recovery flow.
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = false
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  mfa_configuration = "OFF"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}
