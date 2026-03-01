"""YouTube Data API v3 wrapper.

使用 OAuth2 认证（token.json），需要 youtube.readonly scope。
自动刷新过期 token。

API 配额：10,000 units/天
- subscriptions.list: 1 unit
- channels.list: 1 unit
- playlistItems.list: 1 unit
- videos.list: 1 unit
- search.list: 100 units（慎用！）
- commentThreads.list: 1 unit

Setup:
1. 在 Google Cloud Console 创建项目并启用 YouTube Data API v3
2. 创建 OAuth2 Client ID (Desktop App)
3. 下载 client_secret.json
4. 运行 scripts/setup.sh 完成 OAuth 授权，生成 token.json
"""

import logging
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


class YouTubeAPIError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(f"YouTube API error: {message}")


class YouTubeAPI:
    def __init__(self, token_path, client_secret_path=None):
        self.token_path = Path(token_path)
        self.client_secret_path = Path(client_secret_path) if client_secret_path else None
        self.creds = None
        self.service = None
        self._load_credentials()

    def _load_credentials(self):
        """Load and refresh OAuth2 credentials from token.json."""
        if not self.token_path.exists():
            raise YouTubeAPIError(
                f"Token file not found: {self.token_path}\n"
                "Run scripts/setup.sh to complete YouTube OAuth authorization."
            )

        self.creds = Credentials.from_authorized_user_file(str(self.token_path))

        if self.creds.expired and self.creds.refresh_token:
            logger.info("Refreshing expired OAuth token")
            self.creds.refresh(Request())
            with open(self.token_path, "w") as f:
                f.write(self.creds.to_json())
            logger.info("Token refreshed and saved")

        if not self.creds.valid:
            raise YouTubeAPIError("Invalid credentials — run scripts/setup.sh to re-authenticate")

        self.service = build("youtube", "v3", credentials=self.creds)

    def get_subscriptions(self, max_results=50):
        """Get authenticated user's subscriptions. Cost: 1 unit/page."""
        subs = []
        page_token = None
        while True:
            try:
                resp = self.service.subscriptions().list(
                    part="snippet",
                    mine=True,
                    maxResults=min(max_results - len(subs), 50),
                    pageToken=page_token,
                ).execute()
            except Exception as e:
                raise YouTubeAPIError(f"subscriptions.list failed: {e}")

            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                resource = snippet.get("resourceId", {})
                subs.append({
                    "channelId": resource.get("channelId", ""),
                    "title": snippet.get("title", ""),
                })

            page_token = resp.get("nextPageToken")
            if not page_token or len(subs) >= max_results:
                break

        return subs

    def get_channel_upload_playlists(self, channel_ids):
        """Get uploads playlist ID for each channel (batch up to 50). Cost: 1 unit."""
        result = {}
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i + 50]
            try:
                resp = self.service.channels().list(
                    part="contentDetails",
                    id=",".join(batch),
                ).execute()
            except Exception as e:
                raise YouTubeAPIError(f"channels.list failed: {e}")

            for item in resp.get("items", []):
                cid = item.get("id", "")
                uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
                if uploads:
                    result[cid] = uploads

        return result

    def get_recent_uploads(self, playlist_id, max_results=10):
        """Get recent video IDs from uploads playlist. Cost: 1 unit."""
        try:
            resp = self.service.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=max_results,
            ).execute()
        except Exception as e:
            raise YouTubeAPIError(f"playlistItems.list failed: {e}")

        return [
            item.get("contentDetails", {}).get("videoId", "")
            for item in resp.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]

    def get_video_details(self, video_ids):
        """Get full details for videos (batch up to 50). Cost: 1 unit."""
        results = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            try:
                resp = self.service.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(batch),
                ).execute()
            except Exception as e:
                raise YouTubeAPIError(f"videos.list failed: {e}")

            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                results.append({
                    "videoId": item.get("id", ""),
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", ""),
                    "channelId": snippet.get("channelId", ""),
                    "channelTitle": snippet.get("channelTitle", ""),
                    "tags": snippet.get("tags", []),
                    "publishedAt": snippet.get("publishedAt", ""),
                    "duration": item.get("contentDetails", {}).get("duration", ""),
                    "viewCount": int(stats.get("viewCount", 0)),
                    "likeCount": int(stats.get("likeCount", 0)),
                    "commentCount": int(stats.get("commentCount", 0)),
                })

        return results

    def search_videos(self, query, max_results=10):
        """Search for videos by keyword. Cost: 100 units! Use sparingly."""
        try:
            resp = self.service.search().list(
                part="id",
                q=query,
                type="video",
                maxResults=max_results,
                order="date",
            ).execute()
        except Exception as e:
            raise YouTubeAPIError(f"search.list failed: {e}")

        return [
            item.get("id", {}).get("videoId", "")
            for item in resp.get("items", [])
            if item.get("id", {}).get("videoId")
        ]

    def get_comments(self, video_id, max_results=20):
        """Get top-level comments for a video. Cost: 1 unit."""
        try:
            resp = self.service.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=max_results,
                order="relevance",
            ).execute()
        except Exception as e:
            logger.warning("commentThreads.list failed for %s: %s", video_id, e)
            return []

        comments = []
        for item in resp.get("items", []):
            top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            comments.append({
                "author": top.get("authorDisplayName", ""),
                "text": top.get("textDisplay", ""),
                "likeCount": top.get("likeCount", 0),
                "publishedAt": top.get("publishedAt", ""),
            })

        return comments
