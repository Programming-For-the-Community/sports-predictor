#!/usr/bin/env bash
# Generates config/prod.json from live Terraform outputs, in the exact
# shape config/prod.json.example documents and lib/core/config/app_config.dart
# reads via --dart-define-from-file. Run from the repo root, before
# `flutter build web` -- see .github/workflows/frontend_hosting.yml.
#
# config/prod.json is gitignored and never hand-maintained -- it always
# tracks whatever infra is actually live, the same reasoning
# lib/aws-lambdas/nfl/predict never hardcodes a model version.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUTPUT_FILE="$REPO_ROOT/Source/front-end/config/prod.json"

TF_OUTPUTS=$(terraform -chdir="$REPO_ROOT/Terraform" output -json)

API_BASE_URL=$(echo "$TF_OUTPUTS" | jq -r '.api_endpoint.value')
COGNITO_USER_POOL_ID=$(echo "$TF_OUTPUTS" | jq -r '.cognito_user_pool_id.value')
COGNITO_CLIENT_ID=$(echo "$TF_OUTPUTS" | jq -r '.cognito_client_id.value')
AWS_REGION=$(echo "$TF_OUTPUTS" | jq -r '.aws_region.value // "us-east-2"')

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
  }' > "$OUTPUT_FILE"

echo "Wrote $OUTPUT_FILE"
