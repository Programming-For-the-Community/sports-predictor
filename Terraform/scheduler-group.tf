# Shared EventBridge Scheduler group for every schedule in this
# application (currently just NFL ingest; NCAA FB/NBA/NCAA MBB/PGA/F1
# schedules will join this same group as their adapters come online, per
# the Phase 4 registry-driven orchestration plan in
# design/PROJECT_PLAN.md). One explicit group, rather than each schedule
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
