"""
collectors/github/release.py
─────────────────────────────────
GitHubリポジトリの直近のリリース情報（Changelog等）を収集し、
KnowledgePackage に変換するコレクター。

対象 (target) の形式:
    "owner/repo"   例: "ratatui-org/ratatui"

使用エンドポイント:
    GET /repos/{owner}/{repo}/releases   リリース一覧取得

設計メモ:
    - 過去のすべてのリリースを取得すると長すぎるため、直近数件（デフォルト5件）に絞る。
    - バージョン名、公開日時、リリースノート(body)を連結し、Geminiに読ませる。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from collectors.base_collector import BaseCollector, FatalCollectorError
from schemas.knowledge_package import KnowledgeMeta, KnowledgePackage

GITHUB_API_BASE = "https://api.github.com"


class ReleaseCollector(BaseCollector):
    """GitHubリポジトリの直近のリリース（Changelog）を収集するコレクター。"""

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 10.0, limit: int = 5):
        super().__init__(client=client, timeout=timeout)
        self._token = os.environ.get("GITHUB_TOKEN")
        self._limit = limit  # 収集する最新リリースの最大件数

    @property
    def name(self) -> str:
        return "github_release"

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _fetch_logic(self, target: str) -> Dict[str, Any]:
        """
        target: "owner/repo" 形式の文字列。
        最新のリリース情報を取得する。
        """
        owner_repo = self._validate_target(target)

        # per_pageで最新の指定件数のみ取得（GitHub APIはデフォルトで新しい順に返してくれます）
        params = {
            "per_page": self._limit,
        }

        releases_data = await self.request_json(
            f"{GITHUB_API_BASE}/repos/{owner_repo}/releases",
            headers=self._auth_headers(),
            params=params,
        )

        return {
            "target_repo": owner_repo,
            "releases": releases_data,
        }

    @staticmethod
    def _validate_target(target: str) -> str:
        """target が 'owner/repo' 形式であることを検証する。"""
        parts = target.strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            raise FatalCollectorError(
                f"target は 'owner/repo' 形式である必要があります。受け取った値: {target!r}"
            )
        return target.strip("/")

    def _build_package(self, target: str, raw: Dict[str, Any]) -> KnowledgePackage:
        owner_repo = raw["target_repo"]
        releases = raw["releases"]
        
        package_id = f"github_releases_{owner_repo.replace('/', '_')}"
        
        if not releases:
            return KnowledgePackage(
                id=package_id,
                title=f"Releases: {owner_repo}",
                summary="No releases found for this repository.",
                raw_content="",
                capabilities=[],
                constraints=[],
                metadata=KnowledgeMeta(
                    source=self.name,
                    url=f"https://github.com/{owner_repo}/releases",
                    tags=["releases"],
                    raw_stats={"fetched_releases_count": 0},
                ),
            )

        # リリースノートを連結して raw_content を作成
        compiled_text_blocks = []
        
        for release in releases:
            tag_name = release.get("tag_name", "Unknown Tag")
            name = release.get("name") or tag_name
            published_at = release.get("published_at", "Unknown Date")
            body = release.get("body") or "No release notes provided."
            
            # Changelogが長すぎる場合は切り詰める
            if len(body) > 1500:
                body = body[:1500] + "\n... (truncated)"

            block = f"## [{tag_name}] - {name} ({published_at})\n{body}\n"
            compiled_text_blocks.append(block)

        raw_content_str = "\n".join(compiled_text_blocks)
        latest_version = releases[0].get("tag_name", "Unknown")
        
        summary_text = f"Recent {len(releases)} releases for {owner_repo}. Latest version is {latest_version}."

        raw_stats: Dict[str, Any] = {
            "fetched_releases_count": len(releases),
            "latest_version": latest_version,
            "latest_release_date": releases[0].get("published_at"),
        }

        return KnowledgePackage(
            id=package_id,
            title=f"Releases: {owner_repo}",
            summary=summary_text,
            raw_content=raw_content_str,
            capabilities=[],  
            constraints=[],   
            metadata=KnowledgeMeta(
                source=self.name,
                url=f"https://github.com/{owner_repo}/releases",
                tags=["releases", "changelog", latest_version],
                raw_stats=raw_stats,
            ),
        )