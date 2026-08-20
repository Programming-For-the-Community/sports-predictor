# Shared EventBridge Scheduler group holding this application's two
# orchestrator schedules (scheduler-ingest-orchestrator.tf,
# scheduler-training-orchestrator.tf). Both orchestrator state machines
# scan the sport registry at runtime (dynamodb-sport-registry.tf) and fan
# out to whichever sports are active, so onboarding a new sport is a new
# registry row, not a new schedule.

resource "aws_scheduler_schedule_group" "sports_predictor" {
  name = "${var.project}-schedules"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}
