# Shared destination for every aws_cloudwatch_metric_alarm's Critical-tier
# alarm_actions (cloudwatch-alarms.tf) -- no SNS topic existed anywhere in
# this stack before. Warning-tier alarms deliberately have no
# alarm_actions at all; they only ever show up on
# cloudwatch-dashboard-alerts.tf, not in your inbox.
resource "aws_sns_topic" "ops_alerts" {
  name = "${var.project}-ops-alerts"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "observability"
  })
}

# var.alert_email already exists (variables.tf) and is already wired
# through CI (TF_VAR_alert_email <- secrets.ALERT_EMAIL, tf_install.yml) --
# budgets.tf is the only prior consumer. Reused here rather than adding a
# second email variable/secret for the same address.
resource "aws_sns_topic_subscription" "ops_alerts_email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
