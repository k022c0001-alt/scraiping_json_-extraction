# schemas/knowledge_package.py
"""
knowledge_package.py
────────────────────
全てのコレクター（Collector）および解析エンジン（Engine）が準拠すべき
共通のデータ構造（Knowledge Package）を定義するPydanticスキーマ。

このフォーマットで統一してデータを結晶化させることにより、
SQLiteへのクリーンな保存、およびKnowledgeSearcherによる高精度な
ハイブリッド検索（キーワード ＋ メタデータ ＋ 意味解析）が可能になります。
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class KnowledgeMeta(BaseModel):
    """
    ナレッジの出自や収集時の状況を表すメタデータ。
    プラットフォームごとの差異を吸収し、検索時のフィルタリングに活用します。
    """
    source: str = Field(
        ..., 
        description="データソースの識別名。例: 'github', 'qiita', 'huggingface', 'zenn', 'web_scraping'"
    )
    url: Optional[str] = Field(
        None, 
        description="情報の取得元となったオリジナルのURL"
    )
    collected_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="データが収集・作成された日時（ISO 8601フォーマット、UTC）"
    )
    tags: List[str] = Field(
        default_factory=list, 
        description="技術スタックやカテゴリを表すタグのリスト。例: ['Rust', 'TUI', 'CLI', 'Frontend']"
    )
    raw_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="各プラットフォーム固有の定量メトリクス。例: Star数, Fork数, LGTM数, ビュー数など"
    )

class KnowledgePackage(BaseModel):
    """
    自律型AIエージェントの「共通言語」となる高密度ナレッジパッケージ。
    生のスクレイピングデータやAPIレスポンスから、
    Gemini（Engine）が価値ある情報のみを抽出・整理してこの形に変換します。
    """
    id: str = Field(
        ..., 
        description="システム全体で一意となるナレッジの識別子。例: 'github_rust-lang_cargo', 'qiita_article_12345'"
    )
    title: str = Field(
        ..., 
        description="リポジトリ名、記事タイトル、パッケージ名などの人間が識別しやすい名称"
    )
    summary: str = Field(
        ..., 
        description="Gemini（Engine）によって解析・要約された、このナレッジの核心的な解説テキスト（高密度であること）"
    )
    raw_content: str = Field(
        ..., 
        description="README、ドキュメント本文、Issueの議論などから、HTMLタグや不要なノイズをクレンジングしたコアテキスト"
    )
    capabilities: List[str] = Field(
        default_factory=list,
        description="このツールや知識が可能にする『機能・機能要件』のリスト。例: ['ターミナル上でのグラフ描画', '高速な非同期通信']"
    )
    constraints: List[str] = Field(
        default_factory=list,
        description="このツールや知識を利用する上での『制約事項・前提条件』のリスト。例: ['Cargo（Rust環境）必須', 'Node.js v18以上', 'Linux環境のみサポート']"
    )
    metadata: KnowledgeMeta = Field(
        ..., 
        description="出自、URL、タグ、定量スタッツなどのメタデータ"
    )

    class Config:
        """Pydanticの動作設定"""
        # シリアライズ・デシリアライズ時にJSONとオブジェクトの相互変換をスムーズにする設定
        populate_by_name = True
        arbitrary_types_allowed = True

# ────────────────────────────────────────────────────────
# 動作確認・テスト用のサンプルコード
# ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # このスキーマが正しく機能するか、モックデータを入れて検証します。
    sample_package = KnowledgePackage(
        id="github_charmbracelet_ratatui_mock",
        title="ratatui",
        summary="Rustで美しく高度なターミナルユーザーインターフェース（TUI）を構築するためのクックで強力なライブラリ。",
        raw_content="Ratatui is a Rust library to build rich terminal user interfaces and dashboards...",
        capabilities=[
            "端末画面への柔軟なレイアウト配置（Block, Layout, Rect）",
            "多彩なデフォルトウィジェット（グラフ、テーブル、リスト、ゲージ）",
            "CrosstermやTermionといった主要バックエンドとの柔軟な連携"
        ],
        constraints=[
            "Rustのコンパイル環境（Cargo）が必要",
            "Windowsの旧ターミナル（cmd.exe）では一部エスケープシーケンスの発色に制限あり"
        ],
        metadata=KnowledgeMeta(
            source="github",
            url="https://github.com/ratatui-org/ratatui",
            tags=["Rust", "TUI", "CLI", "Dashboard"],
            raw_stats={
                "stars": 8500,
                "forks": 420,
                "language": "Rust",
                "license": "MIT"
            }
        )
    )

    print("✅ Pydanticによる型バリデーション成功！")
    print(f"ID: {sample_package.id}")
    print(f"Title: {sample_package.title}")
    print(f"Tags: {sample_package.metadata.tags}")
    
    # マネージャーやDBに渡すためのシリアライズ確認（JSON文字列化）
    json_string = sample_package.model_dump_json(indent=2)
    print("\n--- シリアライズされたKnowledge Package (JSON形式) ---")
    print(json_string)