"""
The season projection, shaped for the public agent: standings ranked and
filtered by team or conference, stat leaderboards cut to the top few, and
the simulated brackets flattened into one row per matchup with team
abbreviations, so a question about one team or one bracket gets just that.
"""
from __future__ import annotations

from library.assistant.game_list import names_team

STANDINGS_KEYS = ("standings", "driver_standings", "constructor_standings")
# The main bracket per sport, then the NBA Cup's, by the name the answer
# uses; conference tournaments (NCAAMBB, ~30 of them) come only when a
# team or conference is asked for.
BRACKET_KEYS = {"bracket": "Playoffs", "march_madness_bracket": "NCAA tournament", "cup_bracket": "NBA Cup knockout"}
CONFERENCE_BRACKETS = "conference_brackets"
FINAL_ROUNDS = {"final_four": "Final Four", "championship": "Championship", "finals": "Finals", "super_bowl": "Super Bowl"}
LEADERBOARD_TOP = 5
ROUND_ODDS_TOP = 16


def _matches(row: dict, team: str | None, conference: str | None) -> bool:
    if team and not names_team(team, row.get("abbreviation"), row.get("name")):
        return False
    if conference:
        wanted = conference.strip().lower()
        if not any(wanted in (row.get(k) or "").lower() for k in ("conference", "division")):
            return False
    return True


def _strength(row: dict) -> tuple:
    """Model rank where the sport has one, else projected points or wins."""
    if row.get("model_rank") is not None:
        return (0, row["model_rank"])
    projected = row.get("projected_points", row.get("projected_wins", 0)) or 0
    return (1, -projected)


def _without(row: dict, keys: tuple[str, ...]) -> dict:
    return {k: v for k, v in row.items() if k not in keys}


def standings(body: dict, team: str | None, conference: str | None, max_rows: int) -> dict:
    result = {}
    for key in STANDINGS_KEYS:
        rows = [r for r in body.get(key) or [] if _matches(r, team, conference)]
        if not rows and key not in body:
            continue
        rows.sort(key=_strength)
        result[key] = {"count": len(rows), "rows": [_without(r, ("team_id", "entity_id", "color")) for r in rows[:max_rows]]}
        if len(rows) > max_rows:
            result[key]["truncated"] = f"Top {max_rows} of {len(rows)} by projected strength; filter by team or conference for the rest."
    if body.get("leaderboards"):
        result["leaderboards"] = {stat: [_without(r, ("entity_id",)) for r in rows[:LEADERBOARD_TOP]] for stat, rows in body["leaderboards"].items()}
    if (body.get("cup") or {}).get("groups"):
        result["cup_groups"] = {group: [_without(r, ("team_id", "color")) for r in rows if _matches(r, team, None)]
                                for group, rows in body["cup"]["groups"].items()}
    return result


class _Bracket:
    def __init__(self, name: str, bracket: dict):
        self.name = name
        self.bracket = bracket
        self.names = bracket.get("team_names") or {}

    def label(self, team_id) -> str | None:
        if team_id is None:
            return None
        return (self.names.get(str(team_id)) or {}).get("abbreviation") or str(team_id)

    def _side(self, matchup: dict, side: str) -> str:
        label = self.label(matchup.get(f"team_{side}"))
        if label is None:
            return "bye"
        seed, chance = matchup.get(f"seed_{side}"), matchup.get(f"chance_{side}")
        text = f"{label} ({seed})" if seed is not None else label
        return f"{text} {round(chance * 100)}%" if chance is not None else text

    def row(self, group: str | None, round_name: str, matchup: dict) -> dict:
        entry = {"tournament": self.name, "round": round_name, "matchup": f"{self._side(matchup, 'a')} vs {self._side(matchup, 'b')}",
                 "pick": self.label(matchup.get("predicted_winner")), "status": matchup.get("status")}
        if group:
            entry["group"] = group
        if matchup.get("flag"):
            entry["flag"] = matchup["flag"]
        if matchup.get("predicted_wins_a") is not None:
            entry["series"] = f"{matchup['predicted_wins_a']}-{matchup.get('predicted_wins_b', 0)}"
        return entry

    def _rounds(self, group: str | None, rounds: list[dict]) -> list[dict]:
        return [self.row(group, r.get("round", ""), m) for r in rounds for m in r.get("matchups") or []]

    def rows(self) -> list[dict]:
        b = self.bracket
        rows = [self.row(None, "First Four", m) for m in b.get("first_four") or []]
        rows += self._rounds(None, b.get("rounds") or [])
        for group, rounds in (b.get("conferences") or {}).items():
            rows += self._rounds(group, rounds)
        for group, region in (b.get("regions") or {}).items():
            rows += self._rounds(group, region.get("rounds") or [])
        for key, round_name in FINAL_ROUNDS.items():
            rows += [self.row(None, round_name, m) for m in _as_list(b.get(key))]
        return rows

    def round_odds(self) -> list[dict]:
        odds = self.bracket.get("round_odds") or {}
        columns = odds.get("columns") or []
        teams = []
        for group, rows in (odds.get("groups") or {}).items():
            for row in rows:
                chances = {col: round(v * 100, 1) for col, v in zip(columns, row.get("values") or [])}
                teams.append({"team": self.label(row.get("team_id")), "seed": row.get("seed"), "group": group, **chances})
        last = columns[-1] if columns else None
        return sorted(teams, key=lambda t: -(t.get(last) or 0)) if last else teams

    def summary(self, team: str | None, conference: str | None) -> dict:
        rows = [r for r in self.rows() if _row_matches(r, team, conference, self.names)]
        odds = [t for t in self.round_odds() if not team or _team_matches(t["team"], team, self.names)]
        return {"champion": self.label(self.bracket.get("champion")), "simulated_runs": self.bracket.get("simulations"),
                "matchups": rows, "round_odds": odds if team else odds[:ROUND_ODDS_TOP]}


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    return [value] if value else []


def _team_matches(label: str | None, team: str, names: dict) -> bool:
    if label and names_team(team, label, None):
        return True
    return any(n.get("abbreviation") == label and names_team(team, None, n.get("name")) for n in names.values())


def _row_matches(row: dict, team: str | None, conference: str | None, names: dict) -> bool:
    """conference matches a bracket's group (AFC, East) or a conference
    tournament's own name."""
    if conference and conference.strip().lower() not in (row.get("group") or row["tournament"]).lower():
        return False
    if team:
        sides = [part.split(" ")[0] for part in row["matchup"].split(" vs ")]
        return any(_team_matches(side, team, names) for side in sides)
    return True


def _capped_summary(bracket: _Bracket, team: str | None, conference: str | None, max_rows: int) -> dict | None:
    """The bracket's summary, or None when nothing in it matches."""
    summary = bracket.summary(team, conference)
    if not summary["matchups"] and not (team and summary["round_odds"]):
        return None
    summary["count"] = len(summary["matchups"])
    summary["matchups"] = summary["matchups"][:max_rows]
    return summary


def brackets(body: dict, team: str | None, conference: str | None, max_rows: int) -> dict:
    found = [_Bracket(label, body[key]) for key, label in BRACKET_KEYS.items() if body.get(key)]
    conference_brackets = body.get(CONFERENCE_BRACKETS) or []
    if team or conference:
        found += [_Bracket(f"{c.get('conference')} tournament", c.get("bracket") or {}) for c in conference_brackets]
    if not found and not conference_brackets:
        return {"brackets": [], "note": "This sport's projection has no bracket."}
    summaries = {b.name: s for b in found if (s := _capped_summary(b, team, conference, max_rows))}
    result = {"brackets": summaries}
    if conference_brackets and not (team or conference):
        result["conference_tournaments"] = [
            {"conference": c.get("conference"), "projected_champion": _Bracket("", c.get("bracket") or {}).label((c.get("bracket") or {}).get("champion"))}
            for c in conference_brackets
        ]
    return result
