"""
When a game counts as possibly live -- shared by the live-scores pollers
(library.serving.live_scores_common) and the event-list readers
(library.serving.common). Standard library only: the read-only
predict-read Lambda imports this and bundles no HTTP client.
"""
from datetime import datetime, timedelta

# Hard safety cap so a data anomaly (a bad kickoff_time, or a game whose
# status never reaches completed) can't get polled forever -- no real game
# in any of these sports runs anywhere close to this long after its own
# kickoff/tip-off.
POLL_SAFETY_CAP_AFTER_KICKOFF = timedelta(hours=7)


def parse_kickoff(kickoff_time: str) -> datetime:
    # ESPN's own timestamp shape ("...Z").
    return datetime.fromisoformat(kickoff_time.replace("Z", "+00:00"))
