#!/usr/bin/env python3
"""体育赛事日历同步工具"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    sys.exit("需要 pyyaml: pip3 install pyyaml")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
MANUAL_PATH = BASE_DIR / "manual_events.yaml"

CST = timezone(timedelta(hours=8))
UTC = timezone.utc


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_manual() -> Dict[str, Any]:
    if not MANUAL_PATH.exists():
        return {}
    with open(MANUAL_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_json_curl(url: str, timeout: int = 20) -> Dict[str, Any]:
    try:
        res = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-H", "Accept: application/json", url],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(res.stdout)
    except Exception as e:
        print(f"  [warn] 请求失败 ({url[:60]}...): {e}", file=sys.stderr)
        return {}


def parse_dt(s: Optional[str], tz: timezone = CST) -> Optional[datetime]:
    if not s:
        return None
    if s.endswith("Z"):
        s_clean = s[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s_clean, fmt).replace(tzinfo=UTC).astimezone(CST)
            except ValueError:
                continue
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def fetch_espn_soccer_team_events(team_cfg: Dict[str, Any], days_ahead: int) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    team_name = team_cfg.get("name", "Liverpool")
    espn_id = str(team_cfg.get("espn_team_id", "364"))
    leagues = team_cfg.get("leagues", ["eng.1", "uefa.champions", "eng.fa", "eng.league_cup"])

    now = datetime.now(CST)
    cutoff = now + timedelta(days=days_ahead)

    league_names = {
        "eng.1": "English Premier League",
        "uefa.champions": "UEFA Champions League",
        "eng.fa": "English FA Cup",
        "eng.league_cup": "Carabao Cup",
    }

    for l in leagues:
        curr = now
        while curr < cutoff:
            nxt = min(curr + timedelta(days=45), cutoff)
            dates_str = f"{curr.strftime('%Y%m%d')}-{nxt.strftime('%Y%m%d')}"
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{l}/scoreboard?dates={dates_str}&limit=100"
            data = fetch_json_curl(url)
            for ev in data.get("events", []):
                comps = ev.get("competitions", [{}])[0]
                competitors = comps.get("competitors", [])
                team_ids = [str(c.get("id")) for c in competitors]
                team_display_names = [c.get("team", {}).get("displayName", "") for c in competitors]

                if espn_id in team_ids or any(team_name.lower() in name.lower() for name in team_display_names):
                    dt = parse_dt(ev.get("date"))
                    if not dt or dt < now - timedelta(hours=3) or dt > cutoff:
                        continue

                    home_team, away_team = "", ""
                    for c in competitors:
                        if c.get("homeAway") == "home":
                            home_team = c.get("team", {}).get("displayName", "")
                        else:
                            away_team = c.get("team", {}).get("displayName", "")

                    summary = f"{home_team} vs {away_team}" if home_team and away_team else ev.get("name", team_name)
                    venue = comps.get("venue", {}).get("fullName", "")
                    league_title = data.get("leagues", [{}])[0].get("name") or league_names.get(l, l)

                    events.append({
                        "summary": summary,
                        "start": dt,
                        "end": dt + timedelta(hours=2),
                        "league": league_title,
                        "venue": venue,
                        "sport": "Soccer",
                        "source": "espn_soccer",
                    })
            curr = nxt + timedelta(days=1)

    return events


def fetch_espn_nba_team_events(team_cfg: Dict[str, Any], days_ahead: int) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    team_id = team_cfg.get("espn_team_id", "phi")
    now = datetime.now(CST)
    cutoff = now + timedelta(days=days_ahead)

    for st in [1, 2]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/schedule?season=2027&seasontype={st}"
        data = fetch_json_curl(url)
        for ev in data.get("events", []):
            dt = parse_dt(ev.get("date"))
            if not dt or dt < now - timedelta(hours=4) or dt > cutoff:
                continue
            comps = ev.get("competitions", [{}])[0]
            competitors = comps.get("competitors", [])
            home_team, away_team = "", ""
            for c in competitors:
                if c.get("homeAway") == "home":
                    home_team = c.get("team", {}).get("displayName", "")
                else:
                    away_team = c.get("team", {}).get("displayName", "")
            summary = f"{home_team} vs {away_team}" if home_team and away_team else ev.get("name", "NBA Game")
            venue = comps.get("venue", {}).get("fullName", "")
            league_name = "NBA 季前赛" if st == 1 else "NBA"
            events.append({
                "summary": summary,
                "start": dt,
                "end": dt + timedelta(hours=2, minutes=30),
                "league": league_name,
                "venue": venue,
                "sport": "Basketball",
                "source": "espn_nba",
            })
    return events


def fetch_f1_events(ms_cfg: Dict[str, Any], days_ahead: int) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    include_quali = ms_cfg.get("include_qualifying", True)
    include_sprint = ms_cfg.get("include_sprint", True)

    now = datetime.now(CST)
    cutoff = now + timedelta(days=days_ahead)
    url = "https://api.jolpi.ca/ergast/f1/current.json"
    data = fetch_json_curl(url)
    races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

    for r in races:
        race_name = r.get("raceName", "F1 Grand Prix")
        circuit = r.get("Circuit", {}).get("circuitName", "")
        locality = r.get("Circuit", {}).get("Location", {}).get("locality", "")
        venue = f"{circuit} ({locality})" if locality else circuit

        if include_quali and "Qualifying" in r:
            q_info = r["Qualifying"]
            q_dt = parse_dt(f"{q_info.get('date')}T{q_info.get('time', '00:00:00Z')}")
            if q_dt and (now - timedelta(hours=3)) <= q_dt <= cutoff:
                events.append({
                    "summary": f"F1: {race_name} - 排位赛",
                    "start": q_dt,
                    "end": q_dt + timedelta(hours=1, minutes=15),
                    "league": "Formula 1",
                    "venue": venue,
                    "sport": "Motorsport",
                    "source": "jolpica_f1",
                })

        if include_sprint and "Sprint" in r:
            sp_info = r["Sprint"]
            sp_dt = parse_dt(f"{sp_info.get('date')}T{sp_info.get('time', '00:00:00Z')}")
            if sp_dt and (now - timedelta(hours=3)) <= sp_dt <= cutoff:
                events.append({
                    "summary": f"F1: {race_name} - 冲刺赛",
                    "start": sp_dt,
                    "end": sp_dt + timedelta(hours=1),
                    "league": "Formula 1",
                    "venue": venue,
                    "sport": "Motorsport",
                    "source": "jolpica_f1",
                })

        dt = parse_dt(f"{r.get('date')}T{r.get('time', '00:00:00Z')}")
        if dt and (now - timedelta(hours=4)) <= dt <= cutoff:
            events.append({
                "summary": f"F1: {race_name} - 正赛",
                "start": dt,
                "end": dt + timedelta(hours=2),
                "league": "Formula 1",
                "venue": venue,
                "sport": "Motorsport",
                "source": "jolpica_f1",
            })

    return events


def load_manual_events(manual: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    sources = {s["name"]: s for s in config.get("manual_sources", [])}
    for team_name, info in manual.items():
        if not isinstance(info, dict):
            continue
        src = sources.get(team_name, {})
        home_only = src.get("home_only", False)
        default_venue = info.get("home_venue", "")
        for ev in info.get("events", []):
            if home_only and ev.get("home") != team_name:
                continue
            dt = parse_dt(ev["date"])
            if not dt:
                continue
            venue = ev.get("venue") or default_venue
            events.append({
                "summary": f"{ev['home']} vs {ev['away']}",
                "start": dt,
                "end": dt + timedelta(hours=2),
                "league": ev.get("league", info.get("league", "")),
                "venue": venue,
                "sport": "Soccer",
                "source": "manual",
            })
    return events


def gen_ics(events: List[Dict[str, Any]], out_path: str) -> Path:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//sports-calendar//zh//",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:体育赛事",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "X-LIC-LOCATION:Asia/Shanghai",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "DTSTART:19700101T000000",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for ev in sorted(events, key=lambda e: e["start"]):
        dt = ev["start"]
        end = ev.get("end") or (dt + timedelta(hours=2))

        raw_id = f"{dt.isoformat()}-{ev['summary']}"
        uid_hash = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()
        uid = f"{dt.strftime('%Y%m%d')}-{uid_hash[:16]}@sports-calendar"

        desc = f"赛事: {ev['league']}"
        if ev.get("venue"):
            desc += f" | 场馆: {ev['venue']}"
        desc += f" | 来源: {ev['source']}"

        dt_utc = dt.astimezone(UTC)
        end_utc = end.astimezone(UTC)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{ev['summary']}",
            f"DESCRIPTION:{desc}",
        ])
        if ev.get("venue"):
            lines.append(f"LOCATION:{ev['venue']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    out_str = os.path.expanduser(str(out_path))
    out_file = Path(out_str)
    if not out_file.is_absolute():
        out_file = BASE_DIR / out_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\r\n".join(lines), encoding="utf-8")
    return out_file


def collect_all_events(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    days_ahead = config.get("days_ahead", 180)

    for team in config.get("soccer_teams", []):
        print(f"  [Soccer] {team['name']}")
        events += fetch_espn_soccer_team_events(team, days_ahead)

    for team in config.get("nba_teams", []):
        print(f"  [NBA] {team['name']}")
        events += fetch_espn_nba_team_events(team, days_ahead)

    for ms in config.get("motorsports", []):
        print(f"  [Motorsport] {ms['name']}")
        events += fetch_f1_events(ms, days_ahead)

    manual = load_manual()
    print("  [Domestic] manual_events.yaml")
    events += load_manual_events(manual, config)

    seen = set()
    uniq: List[Dict[str, Any]] = []
    for e in events:
        key = (e["summary"], e["start"].isoformat())
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    uniq.sort(key=lambda e: e["start"])
    return uniq


def push_to_gist(ics_path: Path, config: Dict[str, Any]) -> bool:
    gist_id = os.getenv("GIST_ID") or config.get("gist_id", "d94f811403d8b8bfd0f29ffabaeac9e3")
    gist_user = os.getenv("GIST_USER") or config.get("gist_user", "deungjaho")
    raw_url = f"https://gist.githubusercontent.com/{gist_user}/{gist_id}/raw/games.ics"
    webcal_url = f"webcal://gist.githubusercontent.com/{gist_user}/{gist_id}/raw/games.ics"
    try:
        result = subprocess.run(
            ["gh", "gist", "edit", gist_id, str(ics_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"\n[Gist] {raw_url}")
            print(f"[Calendar] {webcal_url}")
            return True
        else:
            print(f"[error] gist 推送失败: {result.stderr}", file=sys.stderr)
            return False
    except FileNotFoundError:
        print("[warn] gh CLI 未安装，跳过 gist 推送", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("[error] gist 推送超时", file=sys.stderr)
        return False


def cmd_sync(config: Dict[str, Any], push_gist: bool = True) -> None:
    print("=== 同步赛事日历 ===")
    events = collect_all_events(config)

    min_threshold = int(config.get("min_events_threshold", 20))
    if len(events) < min_threshold:
        print(f"\n[error] 赛事数量 ({len(events)}) 低于阈值 ({min_threshold})，终止推送", file=sys.stderr)
        sys.exit(1)

    out = gen_ics(events, config.get("output_ics", "games.ics"))
    print(f"\n生成: {len(events)} 场赛事 → {out}")

    if push_gist:
        success = push_to_gist(out, config)
        if not success and os.getenv("GITHUB_ACTIONS"):
            sys.exit(1)


def cmd_list(config: Dict[str, Any]) -> None:
    events = collect_all_events(config)
    now = datetime.now(CST)
    print(f"\n近期赛事 (从 {now.strftime('%Y-%m-%d')} 起):")
    for e in events:
        if e["start"] < now - timedelta(hours=4):
            continue
        flag = "主" if e["source"] == "manual" else "  "
        loc = f"@{e['venue']}" if e.get("venue") else ""
        print(f"  {flag} {e['start'].strftime('%m-%d %a %H:%M')}  {e['summary']:<34}  [{e['league']}] {loc}")


def cmd_status(config: Dict[str, Any]) -> None:
    print("=== 配置状态 ===")
    print(f"输出: {config.get('output_ics')}")
    print(f"天数: {config.get('days_ahead')}")
    print(f"熔断阈值: {config.get('min_events_threshold')}")
    manual = load_manual()
    for name, info in manual.items():
        n = len(info.get("events", [])) if isinstance(info, dict) else 0
        print(f"  {name}: {n} 场")
    ics = Path(os.path.expanduser(str(config.get("output_ics", "games.ics"))))
    if not ics.is_absolute():
        ics = BASE_DIR / ics
    if ics.exists():
        print(f"\n.ics: {ics} ({ics.stat().st_size} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(description="体育赛事日历同步")
    ap.add_argument("--list", action="store_true", help="列出近期赛事")
    ap.add_argument("--status", action="store_true", help="查看状态")
    ap.add_argument("--no-push", action="store_true", help="只生成 .ics，不推送 gist")
    args = ap.parse_args()
    config = load_config()
    if args.status:
        cmd_status(config)
    elif args.list:
        cmd_list(config)
    else:
        cmd_sync(config, push_gist=not args.no_push)


if __name__ == "__main__":
    main()
