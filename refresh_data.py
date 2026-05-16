#!/usr/bin/env python3
"""
refresh_data.py — Orso YouTube Dashboard data fetcher.
Trae datos de todos los canales de la cuenta via YouTube Data API + Analytics API.
Escribe data.json y hace commit+push al repo.

Uso:
    python3 ~/clipflow/yt-dashboard/refresh_data.py
"""

import json
import os
import subprocess
from datetime import date, timedelta, datetime

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_FILE = os.path.expanduser("~/clipflow/auth/youtube_token.json")
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]


def get_creds():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def fmt_duration(iso):
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return "—"
    h, mn, s = (int(x or 0) for x in m.groups())
    if h:
        return f"{h}:{mn:02d}:{s:02d}"
    return f"{mn}:{s:02d}"


def fetch_channel_data(yt, channel_id):
    resp = yt.channels().list(
        part="snippet,statistics,contentDetails",
        id=channel_id
    ).execute()
    items = resp.get("items", [])
    if not items:
        return None
    ch = items[0]
    stats = ch.get("statistics", {})
    snippet = ch.get("snippet", {})
    thumb = snippet.get("thumbnails", {}).get("default", {}).get("url")
    return {
        "id": channel_id,
        "title": snippet.get("title", ""),
        "handle": snippet.get("customUrl", ""),
        "thumbnail": thumb,
        "subscribers": int(stats.get("subscriberCount", 0)),
        "total_views": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
    }


def fetch_analytics(analytics, channel_id, start, end):
    try:
        resp = analytics.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start,
            endDate=end,
            metrics="views,estimatedMinutesWatched,subscribersGained",
        ).execute()
        rows = resp.get("rows", [])
        if rows:
            return {
                "views_28d": int(rows[0][0]),
                "minutes_28d": int(rows[0][1]),
                "subs_gained_28d": int(rows[0][2]),
            }
    except Exception as e:
        print(f"  Analytics error for {channel_id}: {e}")
    return {"views_28d": 0, "minutes_28d": 0, "subs_gained_28d": 0}


def fetch_recent_videos(yt, channel_id, max_results=8):
    try:
        search = yt.search().list(
            part="snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=max_results,
        ).execute()

        video_ids = [i["id"]["videoId"] for i in search.get("items", [])]
        if not video_ids:
            return []

        details = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
        ).execute()

        videos = []
        for v in details.get("items", []):
            snip = v.get("snippet", {})
            stats = v.get("statistics", {})
            dur = v.get("contentDetails", {}).get("duration", "")
            videos.append({
                "id": v["id"],
                "title": snip.get("title", ""),
                "published_at": snip.get("publishedAt", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "duration": fmt_duration(dur),
            })
        return videos
    except Exception as e:
        print(f"  Videos error for {channel_id}: {e}")
        return []


def get_all_channel_ids(yt):
    """Trae todos los canales de la cuenta autenticada."""
    resp = yt.channels().list(part="id,snippet", mine=True).execute()
    return [(i["id"], i["snippet"]["title"]) for i in resp.get("items", [])]


def main():
    print("=== Orso Dashboard Refresh ===")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    creds = get_creds()
    yt = build("youtube", "v3", credentials=creds)
    analytics = build("youtubeAnalytics", "v2", credentials=creds)

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=28)).isoformat()

    print(f"Periodo Analytics: {start} → {end}\n")

    channel_ids = get_all_channel_ids(yt)
    print(f"Canales encontrados: {len(channel_ids)}")
    for cid, title in channel_ids:
        print(f"  · {title} ({cid})")
    print()

    channels = []
    for cid, _ in channel_ids:
        print(f"Procesando: {cid}...")
        data = fetch_channel_data(yt, cid)
        if not data:
            print(f"  Sin datos, saltando.")
            continue
        print(f"  {data['title']} — {data['subscribers']:,} subs")

        analytics_data = fetch_analytics(analytics, cid, start, end)
        print(f"  Views 28d: {analytics_data['views_28d']:,}")

        videos = fetch_recent_videos(yt, cid)
        print(f"  Videos recientes: {len(videos)}")

        channels.append({**data, **analytics_data, "recent_videos": videos})

    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
        "channels": channels,
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\ndata.json escrito — {len(channels)} canal(es).")

    # Commit y push
    repo_dir = os.path.dirname(DATA_FILE)
    cmds = [
        ["git", "-C", repo_dir, "add", "data.json"],
        ["git", "-C", repo_dir, "commit", "-m",
         f"chore: refresh data {date.today().isoformat()}"],
        ["git", "-C", repo_dir, "push", "origin", "main"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"  git: {result.stderr.strip() or result.stdout.strip()}")
        else:
            print(f"  {' '.join(cmd[2:])} ok")

    print("\nDashboard actualizado.")
    print("URL: https://feririarte7-ship-it.github.io/yt-dashboard/")


if __name__ == "__main__":
    main()
