"""
collectors/github/issue.py
─────────────────────────────────
GitHubリポジトリの直近の課題（Issue）や議論を収集し、
KnowledgePackage に変換するコレクター。

対象 (target) の形式:
    "owner/repo"   例: "ratatui-org/ratatui"

使用エンドポイント:
    GET /repos/{owner}/{repo}/issues   直近のIssue一覧取得

設計メモ:
    - 取得するIssueは「更新順 (updated)」で上位数件（デフォルト10件）に絞る。
      これにより、現在アクティブな議論や直近のバグ修正のコンテキストを拾う。
    - Pull Request も Issue API に含まれるが、現状はまとめて収集し、
      raw_content 内にテキストとして連結して Gemini に文脈を推論させる。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from collectors.base_collector import BaseCollector, FatalCollectorError
from schemas.knowledge_package import KnowledgeMeta, KnowledgePackage

GITHUB_API_BASE = "https://api.github.com"


class IssueCollector(BaseCollector):
    """GitHubリポジトリの直近のIssue/PRを収集するコレクター。"""

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 10.0, limit: int = 10):
        super().__init__(client=client, timeout=timeout)
        self._token = os.environ.get("GITHUB_TOKEN")
        self._limit = limit  # 収集するIssueの最大件数

    @property
    def name(self) -> str:
        return "github_issue"

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _fetch_logic(self, target: str) -> Dict[str, Any]:
        """
        target: "owner/repo" 形式の文字列。
        更新順で直近のIssueを取得する。
        """
        owner_repo = self._validate_target(target)

        # 状態問わず(all)、更新日順(updated)で降順(desc)に取得
        params = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": self._limit,
        }

        issues_data = await self.request_json(
            f"{GITHUB_API_BASE}/repos/{owner_repo}/issues",
            headers=self._auth_headers(),
            params=params,
        )

        return {
            "target_repo": owner_repo,
            "issues": issues_data,
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
        issues = raw["issues"]
        
        package_id = f"github_issues_{owner_repo.replace('/', '_')}"
        
        # Issueの内容をテキストとして連結し、Geminiに読ませるための raw_content を作成
        compiled_text_blocks = []
        open_count = 0
        closed_count = 0
        
        for issue in issues:
            state = issue.get("state", "unknown")
            is_pr = "pull_request" in issue
            type_str = "Pull Request" if is_pr else "Issue"
            
            if state == "open":
                open_count += 1
            elif state == "closed":
                closed_count += 1

            title = issue.get("title", "No Title")
            number = issue.get("number", "Unknown")
            body = issue.get("body") or "No description provided."
            
            # 長すぎるbodyは適度に切り詰める（Geminiのトークン節約のため）
            if len(body) > 1000:
                body = body[:1000] + "\n... (truncated)"

            block = f"[{type_str} #{number}] ({state.upper()}) {title}\n{body}\n"
            compiled_text_blocks.append(block)

        raw_content_str = "\n".join(compiled_text_blocks)
        
        summary_text = f"Recent active issues and PRs for {owner_repo}. (Open: {open_count}, Closed: {closed_count})"

        raw_stats: Dict[str, Any] = {
            "fetched_issues_count": len(issues),
            "open_in_fetched": open_count,
            "closed_in_fetched": closed_count,
        }

        return KnowledgePackage(
            id=package_id,
            title=f"Recent Issues: {owner_repo}",
            summary=summary_text,
            raw_content=raw_content_str,
            capabilities=[],  
            constraints=[],   
            metadata=KnowledgeMeta(
                source=self.name,
                url=f"https://github.com/{owner_repo}/issues",
                tags=["issues", "discussions"],
                raw_stats=raw_stats,
            ),
        )