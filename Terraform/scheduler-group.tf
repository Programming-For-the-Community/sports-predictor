# Shared EventBridge Scheduler group holding this application's two
# orchestrator schedules (scheduler-ingest-orchestrator.tf,
# scheduler-training-orchestrator.tf). Onboarding NCAA FB/NBA/NCAA MBB/PGA/F1
# doesn't add new schedules here -- both orchestrator state machines scan
# the sport registry at runtime (dynamodb-sport-registry.tf) and fan out
# to whichever sports are active, so a new sport is a new registry row,
# not a new schedule. One explicit group, rather than each schedule
# implicitly landing in AWS's own "default" group, so this project's
# schedules are grouped and identifiable on their own in the Scheduler
# console.

resource "aws_scheduler_schedule_group" "sports_predictor" {
  name = "${var.project}-schedules"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}
