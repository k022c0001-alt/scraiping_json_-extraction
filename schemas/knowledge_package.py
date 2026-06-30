"""
schemas/knowledge_package.py
─────────────────────────────────
パイプライン全体で流通する「知識パッケージ」の型定義。
Pydantic v2 を使用して型安全・バリデーション自動化を実現する。

設計の要点:
    1. KnowledgePackage: すべてのコレクター出力が従う統一フォーマット
       - raw_content: 元データ（README, Issue本文等の生テキスト）
       - summary: 簡易説明（コレクター段階では基本情報、Gemini段階で高密度化）
       - capabilities: 機能・特徴（Geminiが抽出）
       - constraints: 制約・注意点（Geminiが抽出）
       - metadata: ソース情報・統計情報・タグ

    2. KnowledgeMeta: メタデータ（ソース追跡、検索用タグ、統計情報）

    3. 設計ポイント:
       - JSON シリアライズ可能（ベクトルDB保存時に活用）
       - 任意フィールドを許容（将来の拡張を想定）
       - Enum で型安全な source 定義

使用例:
    # コレクター出力例
    package = KnowledgePackage(
        id="github_ratatui-org_ratatui",
        title="ratatui-org/ratatui",
        summary="TUI framework written in Rust",
        raw_content="# Ratatui\n...",
        capabilities=["TUI rendering", "Event handling"],
        constraints=["Linux/macOS/Windows"],
        metadata=KnowledgeMeta(
            source="github_repository",
            url="https://github.com/ratatui-org/ratatui",
            tags=["rust", "tui", "terminal"],
            raw_stats={"stars": 8000, "forks": 250},
        )
    )

    # JSON 化（ベクトルDB保存用）
    json_str = package.model_dump_json()

    # バリデーション結果確認
    try:
        pkg = KnowledgePackage.model_validate(data)
    except ValidationError as e:
        print(f"バリデーションエラー: {e}")
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


# ────────────────────────────────────────────────────────
# Enum: ソース種別の定義（型安全性と IDE補完）
# ────────────────────────────────────────────────────────
class KnowledgeSource(str, Enum):
    """知識パッケージのソース種別（新しいコレクター追加時にここを拡張）。"""
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_ISSUE = "github_issue"
    GITHUB_RELEASE = "github_release"
    GITHUB_COMMIT = "github_commit"
    QIITA_ARTICLE = "qiita_article"
    HUGGINGFACE_MODEL = "huggingface_model"
    ZENN_ARTICLE = "zenn_article"
    NPM_PACKAGE = "npm_package"
    CUSTOM = "custom"  # カスタムソース用


# ────────────────────────────────────────────────────────
# メタデータモデル
# ────────────────────────────────────────────────────────
class KnowledgeMeta(BaseModel):
    """
    知識パッケージのメタデータ。
    ソース追跡、検索用タグ、統計情報を保持する。
    """

    model_config = ConfigDict(
        extra="allow",  # 将来の拡張を許容
        json_schema_extra={
            "example": {
                "source": "github_repository",
                "url": "https://github.com/owner/repo",
                "tags": ["rust", "tui"],
                "raw_stats": {"stars": 8000, "forks": 250},
                "collected_at": "2026-06-30T18:30:00Z",
            }
        }
    )

    source: KnowledgeSource = Field(
        ...,
        description="知識の出所コレクター名"
    )

    url: Optional[str] = Field(
        default=None,
        description="ソース元への参照URL（GitHub, Qiita等）"
    )

    tags: List[str] = Field(
        default_factory=list,
        description="検索・フィルタリング用タグ（言語、プラットフォーム等）"
    )

    raw_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="ソース固有の統計情報。例: {'stars': 8000, 'forks': 250}"
    )

    collected_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        description="収集日時（ISO 8601）"
    )

    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="データ信頼度スコア（0.0〜1.0）"
    )


# ────────────────────────────────────────────────────────
# コアモデル: KnowledgePackage
# ────────────────────────────────────────────────────────
class KnowledgePackage(BaseModel):
    """
    パイプライン全体で流通する「知識パッケージ」。
    コレクター → 解析エンジン → DB保存 まで、
    同じスキーマで統一されるため、各段階で型チェックが機能する。

    フロー例:
        1. RepositoryCollector が raw_content (README) を埋める
        2. TerminalEngine (Gemini) が summary, capabilities, constraints を補完
        3. KnowledgeSearcher が ChromaDB に保存
        4. OFAPlanner がクエリ時に検索・参照
    """

    model_config = ConfigDict(
        extra="allow",  # 将来の拡張フィールドを許容
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None,
        },
        json_schema_extra={
            "example": {
                "id": "github_ratatui-org_ratatui",
                "title": "ratatui-org/ratatui",
                "summary": "A Rust library for building rich TUIs",
                "raw_content": "# Ratatui\n\nA Rust library...",
                "capabilities": ["TUI rendering", "Event handling", "Layout system"],
                "constraints": ["Requires Rust 1.70+", "Terminal support"],
                "metadata": {
                    "source": "github_repository",
                    "url": "https://github.com/ratatui-org/ratatui",
                    "tags": ["rust", "tui", "terminal"],
                    "raw_stats": {"stars": 8000, "forks": 250}
                }
            }
        }
    )

    # ── 必須フィールド ───────────────────────────────
    id: str = Field(
        ...,
        description="ユニークな知識パッケージID。形式例: 'github_owner_repo' または 'qiita_article_id'"
    )

    title: str = Field(
        ...,
        description="知識の表題。リポジトリ名、記事タイトル等"
    )

    # ── 段階的に充実するフィールド ───────────────────
    summary: str = Field(
        default="",
        description="知識の要約。コレクター段階では簡易説明、Gemini解析後に高密度化"
    )

    raw_content: str = Field(
        default="",
        description="元データ（README全文、Issue本文、記事本体等）。Gemini解析の入力"
    )

    # ── Gemini 解析結果（初期は空） ──────────────────
    capabilities: List[str] = Field(
        default_factory=list,
        description="機能・特徴のリスト。Gemini が raw_content から自動抽出"
    )

    constraints: List[str] = Field(
        default_factory=list,
        description="制約・注意点のリスト。Gemini が raw_content から自動抽出"
    )

    # ── メタデータ ────────────────────────────────
    metadata: KnowledgeMeta = Field(
        ...,
        description="メタデータ（ソース、URL、タグ、統計情報等）"
    )

    # ── 処理状態追跡フィールド（オプション） ──────────
    version: int = Field(
        default=1,
        description="スキーマバージョン（将来の互換性維持用）"
    )

    # ── 処理フラグ ────────────────────────────────
    is_analyzed: bool = Field(
        default=False,
        description="Gemini解析済みかどうか"
    )

    is_embedded: bool = Field(
        default=False,
        description="ベクトル化済みかどうか（ChromaDB登録済み）"
    )

    # ── 処理エラー情報（デバッグ用） ──────────────────
    processing_errors: List[str] = Field(
        default_factory=list,
        description="処理中に発生したエラーメッセージ（デバッグ用）"
    )

    # ──────────────────────────────────────────────
    # ユーティリティメソッド
    # ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """JSON シリアライズ可能な辞書に変換（ベクトルDB保存用）。"""
        return self.model_dump(mode="json", exclude_none=False)

    def to_json(self) -> str:
        """JSON 文字列に変換。"""
        return self.model_dump_json(indent=2, exclude_none=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KnowledgePackage:
        """辞書から復元（DB読み込み時等）。"""
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, json_str: str) -> KnowledgePackage:
        """JSON 文字列から復元。"""
        import json
        data = json.loads(json_str)
        return cls.model_validate(data)

    def add_error(self, error_msg: str) -> None:
        """処理エラーを記録。"""
        self.processing_errors.append(error_msg)

    def mark_analyzed(self) -> None:
        """Gemini解析済みとしてマーク。"""
        self.is_analyzed = True

    def mark_embedded(self) -> None:
        """ベクトル化済みとしてマーク。"""
        self.is_embedded = True

    def has_content(self) -> bool:
        """実質的なコンテンツを持っているか（raw_content か capabilities があるか）。"""
        return bool(self.raw_content or self.capabilities)

    def content_length(self) -> int:
        """総コンテンツ量（トークン数概算用）。"""
        return len(self.raw_content) + len(" ".join(self.capabilities)) + len(" ".join(self.constraints))


# ────────────────────────────────────────────────────────
# コレクション用の型定義
# ────────────────────────────────────────────────────────
class KnowledgePackageCollection(BaseModel):
    """複数の KnowledgePackage をまとめて扱う場合用。"""

    model_config = ConfigDict(extra="allow")

    packages: List[KnowledgePackage] = Field(
        default_factory=list,
        description="知識パッケージのリスト"
    )

    total_count: int = Field(
        default=0,
        description="総パッケージ数"
    )

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="コレクション作成日時"
    )

    def add_package(self, package: KnowledgePackage) -> None:
        """パッケージを追加。"""
        self.packages.append(package)
        self.total_count += 1

    def add_packages(self, packages: List[KnowledgePackage]) -> None:
        """複数パッケージを一括追加。"""
        self.packages.extend(packages)
        self.total_count += len(packages)

    def filter_by_source(self, source: KnowledgeSource) -> List[KnowledgePackage]:
        """ソース別にフィルタリング。"""
        return [pkg for pkg in self.packages if pkg.metadata.source == source]

    def filter_by_tag(self, tag: str) -> List[KnowledgePackage]:
        """タグでフィルタリング。"""
        return [pkg for pkg in self.packages if tag in pkg.metadata.tags]

    def analyzed_count(self) -> int:
        """解析済みパッケージ数。"""
        return sum(1 for pkg in self.packages if pkg.is_analyzed)

    def to_json(self) -> str:
        """JSON文字列に変換。"""
        return self.model_dump_json(indent=2, exclude_none=False)
