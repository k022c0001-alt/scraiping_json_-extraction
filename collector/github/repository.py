"""
collectors/github/repository.py
─────────────────────────────────
GitHubリポジトリの基本情報（Star, Fork, 言語, ライセンス, トピック等）と
README本文を収集し、KnowledgePackage に変換するコレクター。

対象 (target) の形式:
    "owner/repo"   例: "ratatui-org/ratatui"

使用エンドポイント:
    GET /repos/{owner}/{repo}          リポジトリ基本情報
    GET /repos/{owner}/{repo}/readme   README本文（raw形式）

設計メモ:
    - capabilities / constraints はこの段階では空リストのままにする。
      これらは後段の analyzers/terminal_engine.py（Gemini解析エンジン）が
      raw_content（README全文）を読んで生成する責務を持つため、
      ここで機械的な文言を埋めてしまうと二重管理・矛盾の元になる。
    - summary もこの段階では repo の description をそのまま使う簡易的な値。
      Geminiエンジンがより高密度な要約に上書きすることを想定している。
    - README が存在しないリポジトリ（404）はエラーではなく
      「READMEなし」という正常な状態として扱い、raw_content を空文字にする。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from collectors.base_collector import BaseCollector, FatalCollectorError
from schemas.knowledge_package import KnowledgeMeta, KnowledgePackage

GITHUB_API_BASE = "https://api.github.com"


class RepositoryCollector(BaseCollector):
    """GitHubリポジトリの基本情報 + README を収集するコレクター。"""

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 10.0):
        super().__init__(client=client, timeout=timeout)
        # GITHUB_TOKEN が無くてもパブリックAPIの範囲では動く（レート制限は厳しくなる）
        self._token = os.environ.get("GITHUB_TOKEN")

    @property
    def name(self) -> str:
        return "github_repository"

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _fetch_logic(self, target: str) -> Dict[str, Any]:
        """
        target: "owner/repo" 形式の文字列。
        repo基本情報とREADMEの両方を取得し、1つのdictにまとめて返す。
        """
        owner_repo = self._validate_target(target)

        repo_info = await self.request_json(
            f"{GITHUB_API_BASE}/repos/{owner_repo}",
            headers=self._auth_headers(),
        )

        readme_text = await self._fetch_readme(owner_repo)

        return {
            "repo_info": repo_info,
            "readme_text": readme_text,
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

    async def _fetch_readme(self, owner_repo: str) -> str:
        """
        README本文をプレーンテキストで取得する。
        README が存在しない場合（404）は空文字を返す（エラーにしない）。
        その他のエラーは通常通り上位（_fetch_with_retry）に伝播させてリトライ対象にする。
        """
        headers = self._auth_headers()
        headers["Accept"] = "application/vnd.github.raw"

        try:
            response = await self._client.get(
                f"{GITHUB_API_BASE}/repos/{owner_repo}/readme",
                headers=headers,
            )
        except httpx.TimeoutException as e:
            from collectors.base_collector import TransientError
            raise TransientError(f"README取得でタイムアウトしました: {owner_repo}") from e
        except httpx.TransportError as e:
            from collectors.base_collector import TransientError
            raise TransientError(f"README取得で接続エラーが発生しました: {owner_repo}") from e

        if response.status_code == 404:
            # READMEが存在しないリポジトリは普通にあるので、エラーにせず空文字で返す
            return ""

        if response.status_code == 429:
            from collectors.base_collector import RateLimitError
            retry_after_header = response.headers.get("Retry-After")
            retry_after = float(retry_after_header) if retry_after_header else None
            raise RateLimitError(f"README取得でレートリミットに達しました: {owner_repo}", retry_after=retry_after)

        if response.status_code >= 500:
            from collectors.base_collector import TransientError
            raise TransientError(f"README取得でサーバーエラー (Status: {response.status_code}): {owner_repo}")

        if response.status_code != 200:
            # 401/403等。リポジトリ情報自体は取れているはずなので致命的エラーにはせず、
            # README無しとして握る（プライベートリポジトリ等で起こりうる）
            return ""

        return response.text

    def _build_package(self, target: str, raw: Dict[str, Any]) -> KnowledgePackage:
        repo_info = raw["repo_info"]
        readme_text = raw["readme_text"]

        owner_repo = target.strip("/")
        full_name = repo_info.get("full_name", owner_repo)
        package_id = f"github_{full_name.replace('/', '_')}"

        license_info = repo_info.get("license") or {}
        license_name = license_info.get("spdx_id") or license_info.get("name")

        topics = list(repo_info.get("topics", []))
        language = repo_info.get("language")
        # トピックと主要言語をタグとして統合
        # （大文字小文字の違いだけの重複、例: topics=['rust'] と language='Rust' は同一視して排除）
        tags = list(topics)
        if language and language.lower() not in {t.lower() for t in topics}:
            tags.append(language)

        raw_stats: Dict[str, Any] = {
            "stars": repo_info.get("stargazers_count"),
            "forks": repo_info.get("forks_count"),
            "watchers": repo_info.get("watchers_count"),
            "open_issues": repo_info.get("open_issues_count"),
            "language": language,
            "license": license_name,
            "archived": repo_info.get("archived"),
            "default_branch": repo_info.get("default_branch"),
            "pushed_at": repo_info.get("pushed_at"),
            "created_at": repo_info.get("created_at"),
        }

        return KnowledgePackage(
            id=package_id,
            title=full_name,
            # 簡易summary。後段のGeminiエンジンが本格的な要約に上書きする想定。
            summary=repo_info.get("description") or "",
            raw_content=readme_text,
            capabilities=[],  # Geminiエンジン側で生成する
            constraints=[],   # Geminiエンジン側で生成する
            metadata=KnowledgeMeta(
                source=self.name,
                url=repo_info.get("html_url"),
                tags=tags,
                raw_stats=raw_stats,
            ),
        )