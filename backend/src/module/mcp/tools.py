import json
import logging

from mcp import types

from module.conf import VERSION
from module.downloader import DownloadClient
from module.manager import SeasonCollector, TorrentManager
from module.models import Bangumi, BangumiUpdate, RSSItem
from module.parser.analyser.tmdb_parser import tmdb_parser
from module.rss import RSSAnalyser, RSSEngine
from module.searcher import SearchTorrent

logger = logging.getLogger(__name__)

TOOLS = [
    types.Tool(
        name="list_anime",
        description="List all tracked anime subscriptions. Returns title, season, status, and episode offset for each.",
        inputSchema={
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "If true, only return active (non-disabled) anime",
                    "default": False,
                },
            },
        },
    ),
    types.Tool(
        name="get_anime",
        description="Get detailed information about a specific anime subscription by its ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The anime/bangumi ID",
                },
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="search_anime",
        description="Search for anime torrents across torrent sites (Mikan, DMHY, Nyaa). Returns available anime matching the keywords.",
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "Search keywords (e.g. anime title)",
                },
                "site": {
                    "type": "string",
                    "description": "Torrent site to search",
                    "enum": ["mikan", "dmhy", "nyaa"],
                    "default": "mikan",
                },
            },
            "required": ["keywords"],
        },
    ),
    types.Tool(
        name="subscribe_anime",
        description="Subscribe to an anime series by providing its RSS link. Analyzes the RSS feed and sets up automatic downloading.",
        inputSchema={
            "type": "object",
            "properties": {
                "rss_link": {
                    "type": "string",
                    "description": "RSS feed URL for the anime (obtained from search_anime results)",
                },
                "parser": {
                    "type": "string",
                    "description": "RSS parser type",
                    "enum": ["mikan", "dmhy", "nyaa"],
                    "default": "mikan",
                },
            },
            "required": ["rss_link"],
        },
    ),
    types.Tool(
        name="unsubscribe_anime",
        description="Unsubscribe from an anime. Can either disable (keeps data) or fully delete the subscription.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The anime/bangumi ID to unsubscribe",
                },
                "delete": {
                    "type": "boolean",
                    "description": "If true, permanently delete the subscription. If false, just disable it.",
                    "default": False,
                },
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="list_downloads",
        description="Show current torrent download status from the download client (qBittorrent/Aria2).",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by download status",
                    "enum": ["all", "downloading", "completed", "paused"],
                    "default": "all",
                },
            },
        },
    ),
    types.Tool(
        name="list_rss_feeds",
        description="List all configured RSS feeds with their connection status and health information.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="get_program_status",
        description="Get the current program status including version, running state, and first-run flag.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="refresh_feeds",
        description="Trigger an immediate refresh of all RSS feeds to check for new episodes.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="update_anime",
        description="Update settings for a tracked anime (episode offset, season offset, filters, etc.).",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The anime/bangumi ID to update",
                },
                "episode_offset": {
                    "type": "integer",
                    "description": "Episode number offset for renaming",
                },
                "season_offset": {
                    "type": "integer",
                    "description": "Season number offset for renaming",
                },
                "season": {
                    "type": "integer",
                    "description": "Season number",
                },
                "filter": {
                    "type": "string",
                    "description": "Comma-separated filter patterns to exclude",
                },
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="get_anime_info",
        description=(
            "Look up an anime title on TMDB to get metadata: official title, "
            "season count, latest season, airing status, overview, and poster. "
            "Useful for checking details before subscribing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Anime title to search (Japanese, Chinese or English)",
                },
                "language": {
                    "type": "string",
                    "description": "Preferred language for returned metadata",
                    "enum": ["zh", "jp", "en"],
                    "default": "zh",
                },
            },
            "required": ["title"],
        },
    ),
    types.Tool(
        name="diagnose_subscription",
        description=(
            "Diagnose a subscription to identify potential problems: "
            "checks whether the RSS feed is reachable, whether any recent "
            "downloads exist, whether the subscription is active, and "
            "whether it has been flagged for review."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The anime/bangumi ID to diagnose",
                },
            },
            "required": ["id"],
        },
    ),
]


def _bangumi_to_dict(b: Bangumi) -> dict:
    return {
        "id": b.id,
        "official_title": b.official_title,
        "title_raw": b.title_raw,
        "season": b.season,
        "group_name": b.group_name,
        "dpi": b.dpi,
        "source": b.source,
        "subtitle": b.subtitle,
        "episode_offset": b.episode_offset,
        "season_offset": b.season_offset,
        "filter": b.filter,
        "rss_link": b.rss_link,
        "poster_link": b.poster_link,
        "added": b.added,
        "save_path": b.save_path,
        "deleted": b.deleted,
        "archived": b.archived,
        "eps_collect": b.eps_collect,
    }


async def handle_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [
            types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
        ]
    except Exception as e:
        logger.exception("[MCP] Tool %s failed", name)
        return [
            types.TextContent(
                type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False)
            )
        ]


async def _dispatch(name: str, args: dict) -> dict | list:
    if name == "list_anime":
        return _list_anime(args.get("active_only", False))
    elif name == "get_anime":
        return _get_anime(args["id"])
    elif name == "search_anime":
        return await _search_anime(args["keywords"], args.get("site", "mikan"))
    elif name == "subscribe_anime":
        return await _subscribe_anime(args["rss_link"], args.get("parser", "mikan"))
    elif name == "unsubscribe_anime":
        return await _unsubscribe_anime(args["id"], args.get("delete", False))
    elif name == "list_downloads":
        return await _list_downloads(args.get("status", "all"))
    elif name == "list_rss_feeds":
        return _list_rss_feeds()
    elif name == "get_program_status":
        return _get_program_status()
    elif name == "refresh_feeds":
        return await _refresh_feeds()
    elif name == "update_anime":
        return await _update_anime(args)
    elif name == "get_anime_info":
        return await _get_anime_info(args["title"], args.get("language", "zh"))
    elif name == "diagnose_subscription":
        return await _diagnose_subscription(args["id"])
    else:
        return {"error": f"Unknown tool: {name}"}


def _list_anime(active_only: bool) -> list[dict]:
    with TorrentManager() as manager:
        if active_only:
            items = manager.search_all_bangumi()
        else:
            items = manager.bangumi.search_all()
    return [_bangumi_to_dict(b) for b in items]


def _get_anime(bangumi_id: int) -> dict:
    with TorrentManager() as manager:
        result = manager.search_one(bangumi_id)
    if isinstance(result, Bangumi):
        return _bangumi_to_dict(result)
    return {"error": result.msg_en}


async def _search_anime(keywords: str, site: str) -> list[dict]:
    keyword_list = keywords.split()
    results = []
    async with SearchTorrent() as st:
        async for item_json in st.analyse_keyword(keywords=keyword_list, site=site):
            results.append(json.loads(item_json))
            if len(results) >= 20:
                break
    return results


async def _subscribe_anime(rss_link: str, parser: str) -> dict:
    analyser = RSSAnalyser()
    rss = RSSItem(url=rss_link, parser=parser)
    data = await analyser.link_to_data(rss)
    if not isinstance(data, Bangumi):
        return {"error": data.msg_en if hasattr(data, "msg_en") else str(data)}
    resp = await SeasonCollector.subscribe_season(data, parser=parser)
    return {"status": resp.status, "message": resp.msg_en}


async def _unsubscribe_anime(bangumi_id: int, delete: bool) -> dict:
    with TorrentManager() as manager:
        if delete:
            resp = await manager.delete_rule(bangumi_id)
        else:
            resp = await manager.disable_rule(bangumi_id)
    return {"status": resp.status, "message": resp.msg_en}


async def _list_downloads(status: str) -> list[dict]:
    status_filter = None if status == "all" else status
    async with DownloadClient() as client:
        torrents = await client.get_torrent_info(
            status_filter=status_filter, category="Bangumi"
        )
    return [
        {
            "name": t.get("name", ""),
            "size": t.get("size", 0),
            "progress": t.get("progress", 0),
            "state": t.get("state", ""),
            "dlspeed": t.get("dlspeed", 0),
            "upspeed": t.get("upspeed", 0),
            "eta": t.get("eta", 0),
        }
        for t in torrents
    ]


def _list_rss_feeds() -> list[dict]:
    with RSSEngine() as engine:
        feeds = engine.rss.search_all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "url": f.url,
            "aggregate": f.aggregate,
            "parser": f.parser,
            "enabled": f.enabled,
            "connection_status": f.connection_status,
            "last_checked_at": f.last_checked_at,
            "last_error": f.last_error,
        }
        for f in feeds
    ]


def _get_program_status() -> dict:
    from module.api.program import program

    return {
        "version": VERSION,
        "running": program.is_running,
        "first_run": program.first_run,
    }


async def _refresh_feeds() -> dict:
    async with DownloadClient() as client:
        with RSSEngine() as engine:
            await engine.refresh_rss(client)
    return {"status": True, "message": "RSS feeds refreshed successfully"}


async def _update_anime(args: dict) -> dict:
    bangumi_id = args["id"]
    with TorrentManager() as manager:
        existing = manager.bangumi.search_id(bangumi_id)
        if not existing:
            return {"error": f"Anime with id {bangumi_id} not found"}

        update_data = BangumiUpdate(**existing.model_dump())
        if "episode_offset" in args:
            update_data.episode_offset = args["episode_offset"]
        if "season_offset" in args:
            update_data.season_offset = args["season_offset"]
        if "season" in args:
            update_data.season = args["season"]
        if "filter" in args:
            update_data.filter = args["filter"]

        resp = await manager.update_rule(bangumi_id, update_data)
    return {"status": resp.status, "message": resp.msg_en}


async def _get_anime_info(title: str, language: str) -> dict:
    info = await tmdb_parser(title, language)
    if info is None:
        return {"error": f"No TMDB result found for '{title}'"}
    return {
        "id": info.id,
        "title": info.title,
        "original_title": info.original_title,
        "year": info.year,
        "last_season": info.last_season,
        "season_count": len([s for s in info.season if s.get("season") != "Specials"]),
        "series_status": info.series_status,
        "poster_link": info.poster_link,
        "season_episode_counts": info.season_episode_counts,
    }


async def _diagnose_subscription(bangumi_id: int) -> dict:
    with TorrentManager() as manager:
        bangumi = manager.bangumi.search_id(bangumi_id)
        if not bangumi:
            return {"error": f"Anime with id {bangumi_id} not found"}

        recent_torrents = manager.torrent.search_all_by_bangumi_id(bangumi_id)

    issues = []
    suggestions = []

    if bangumi.deleted:
        issues.append("Subscription has been deleted")
    if bangumi.archived:
        issues.append("Subscription is archived")
    if not bangumi.added:
        issues.append("Subscription is disabled (added=False)")
        suggestions.append("Re-enable the subscription in the WebUI")

    # Check RSS feed health
    with RSSEngine() as engine:
        rss_items = engine.rss.search_all()
    matching_rss = [r for r in rss_items if bangumi.rss_link and r.url == bangumi.rss_link]
    if not matching_rss:
        issues.append("RSS feed not found in feed list")
        suggestions.append("Check that the RSS link is still valid")
    else:
        rss = matching_rss[0]
        if rss.connection_status == "error":
            issues.append(f"RSS feed last check failed: {rss.last_error}")
            suggestions.append("Verify the Mikan/RSS source is reachable")

    if not recent_torrents:
        issues.append("No torrents have been downloaded for this subscription")
        suggestions.append("Check filter settings or trigger a manual RSS refresh")

    return {
        "id": bangumi_id,
        "title": bangumi.official_title,
        "active": bangumi.added and not bangumi.deleted and not bangumi.archived,
        "healthy": len(issues) == 0,
        "issues": issues,
        "suggestions": suggestions,
        "torrent_count": len(recent_torrents) if recent_torrents else 0,
    }
