#!/usr/bin/env bash
# Generates config/prod.json from the values passed in as env vars, in the
# exact shape config/prod.json.example documents and
# lib/core/config/app_config.dart reads via --dart-define-from-file. Run
# from Source/front-end -- see .github/workflows/frontend_hosting.yml,
# which passes these in from frontend_deploy.yml's `infra` job outputs
# (Terraform outputs, read once there -- not re-read here).
#
# config/prod.json is gitignored and never hand-maintained -- it always
# tracks whatever infra is actually live.
set -euo pipefail

: "${API_BASE_URL:?API_BASE_URL is required}"
: "${COGNITO_USER_POOL_ID:?COGNITO_USER_POOL_ID is required}"
: "${COGNITO_CLIENT_ID:?COGNITO_CLIENT_ID is required}"
AWS_REGION="${AWS_REGION:-us-east-2}"

jq -n \
  --arg apiBaseUrl "$API_BASE_URL" \
  --arg cognitoUserPoolId "$COGNITO_USER_POOL_ID" \
  --arg cognitoClientId "$COGNITO_CLIENT_ID" \
  --arg awsRegion "$AWS_REGION" \
  '{
    API_BASE_URL: $apiBaseUrl,
    COGNITO_USER_POOL_ID: $cognitoUserPoolId,
    COGNITO_CLIENT_ID: $cognitoClientId,
    AWS_REGION: $awsRegion
  }' > config/prod.json

echo "Wrote config/prod.json"
