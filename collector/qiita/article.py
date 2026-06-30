"""
collectors/qiita/article.py
─────────────────────────────────
Qiitaの技術記事（Item）を収集し、KnowledgePackage に変換するコレクター。

対象 (target) の形式:
    "{item_id}"   例: "c686397e4a0f4f11683d" (記事URLの末尾の20桁の英数字)

使用エンドポイント:
    GET /items/{item_id}   記事の詳細情報取得

設計メモ:
    - Qiitaの本文（body）はMarkdown形式で取得できるため、そのまま raw_content に入れる。
      HTML（rendered_body）よりもLLMにとってノイズが少なく解析しやすいため。
    - タグは [{"name": "Python", ...}, ...] のような構造なので、名前だけを抽出してリスト化する。
    - 認証トークン（QIITA_ACCESS_TOKEN）がなくてもパブリック記事は取得可能だが、
      API制限が厳しいため環境変数からの読み込みをサポートする。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

import httpx

from collectors.base_collector import BaseCollector, FatalCollectorError
from schemas.knowledge_package import KnowledgeMeta, KnowledgePackage

QIITA_API_BASE = "https://qiita.com/api/v2"


class ArticleCollector(BaseCollector):
    """Qiitaの技術記事（Markdown本文とメタデータ）を収集するコレクター。"""

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 10.0):
        super().__init__(client=client, timeout=timeout)
        # Qiitaの個人用アクセストークン
        self._token = os.environ.get("QIITA_ACCESS_TOKEN")

    @property
    def name(self) -> str:
        return "qiita_article"

    def _auth_headers(self) -> Dict[str, str]:
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _fetch_logic(self, target: str) -> Dict[str, Any]:
        """
        target: 記事のID (20桁の英数字)。
        Qiita APIから記事のJSONデータを取得する。
        """
        item_id = self._validate_target(target)

        article_data = await self.request_json(
            f"{QIITA_API_BASE}/items/{item_id}",
            headers=self._auth_headers(),
        )

        return article_data

    @staticmethod
    def _validate_target(target: str) -> str:
        """targetがQiitaの記事IDらしい形式（20桁前後の英数字）か簡易検証する。"""
        # URLを丸ごと渡された場合へのフェイルセーフ（末尾のIDだけ抽出）
        if "qiita.com" in target:
            target = target.rstrip("/").split("/")[-1]
            
        target = target.strip()
        # 通常20桁の16進数ライクな文字列だが、仕様変更に備えて少し緩めにチェック
        if not re.match(r"^[a-zA-Z0-9]{10,32}$", target):
            raise FatalCollectorError(
                f"target はQiitaの記事ID(英数字)である必要があります。受け取った値: {target!r}"
            )
        return target

    def _build_package(self, target: str, raw: Dict[str, Any]) -> KnowledgePackage:
        item_id = self._validate_target(target)
        package_id = f"qiita_{item_id}"
        
        # タイトル
        title = raw.get("title", f"Qiita Article {item_id}")
        
        # Qiitaのタグはリスト内に辞書が入っている: [{"name": "Python", "versions": []}, ...]
        raw_tags = raw.get("tags", [])
        tags = [tag["name"] for tag in raw_tags if "name" in tag]
        
        # ユーザー情報（著者）
        user_info = raw.get("user") or {}
        author_id = user_info.get("id", "Unknown Author")
        
        # 本文（Markdown形式）
        body_markdown = raw.get("body") or ""

        # 統計情報
        raw_stats: Dict[str, Any] = {
            "likes_count": raw.get("likes_count", 0),
            "stocks_count": raw.get("stocks_count", 0),
            "comments_count": raw.get("comments_count", 0),
            "page_views_count": raw.get("page_views_count"), # 取得者本人の記事のみ入る
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "author_id": author_id,
        }

        # 簡易サマリ
        summary_text = f"Qiita article '{title}' by {author_id}. Tags: {', '.join(tags)}."

        return KnowledgePackage(
            id=package_id,
            title=title,
            summary=summary_text,
            raw_content=body_markdown,
            capabilities=[],  # TerminalEngine(Gemini)に任せる
            constraints=[],   # TerminalEngine(Gemini)に任せる
            metadata=KnowledgeMeta(
                source=self.name,
                url=raw.get("url", f"https://qiita.com/items/{item_id}"),
                tags=tags,
                raw_stats=raw_stats,
            ),
        )