"""
terminal_workflow.py
─────────────────────────────────
OFA (Open-source Foundation Agent) のデータ取り込みパイプライン。
収集、解析、永続化（ベクトルDBへの保存）をシームレスに実行する。

役割（15アプリ構成の該当箇所）:
    - 1〜5番のパイプライン統合オーケストレーター

実行方法:
    python terminal_workflow.py --source github --target ratatui-org/ratatui
    python terminal_workflow.py --source qiita --target c686397e4a0f4f11683d
"""

from __future__ import annotations

import asyncio
import argparse
import logging
import sys
from typing import List, Optional

from collectors.manager import CollectionManager
from analyzers.terminal_engine import TerminalEngine
from core.knowledge_searcher import KnowledgeSearcher
from schemas.knowledge_package import KnowledgePackage

# ログ出力の一元設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ofa_ingestion.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("OFA.Workflow")


class IngestionWorkflow:
    """収集から記憶までのパイプラインを統括するワークフロークラス"""

    def __init__(self):
        # 解析エンジンと検索（DB）エンジンの初期化
        self.terminal_engine = TerminalEngine()
        self.knowledge_searcher = KnowledgeSearcher()

    async def execute(self, target: str, source: Optional[str] = None) -> bool:
        """
        指定されたターゲットの知識取り込みを実行する。

        Args:
            target: 収集対象（"owner/repo" または Qiitaの "item_id"）
            source: ソースの制限（"github" または "qiita"）。Noneの場合は全ソースから自動判定。
        """
        logger.info("==================================================")
        logger.info("[Workflow] パイプラインを開始します。 Target: %s, Source: %s", target, source or "Auto")
        logger.info("==================================================")

        # ── 1. 収集フェーズ (1. スクレイピングアプリ) ──
        logger.info("[Workflow] 1/3 データを収集中...")
        raw_packages: List[KnowledgePackage] = []
        
        async with CollectionManager() as manager:
            if source:
                # ソースが明示されている場合は、そのソースのコレクターのみ動かす
                # GitHubとQiitaでフォーマットを分けるために collect_by_source を活用
                raw_packages = await manager.collect_by_source({source: target})
            else:
                # ソース未指定の場合は、デフォルトで一括収集（主にGitHub形式を想定）
                raw_packages = await manager.collect_all(target)

        if not raw_packages:
            logger.error("[Workflow] データの収集に失敗したか、対象が見つかりませんでした。")
            return False

        logger.info("[Workflow] 収集完了。計 %d 件の生データを取得しました。", len(raw_packages))

        # ── 2. 解析・クレンジングフェーズ (2. ノイズ除去 / 3. JSON変換) ──
        logger.info("[Workflow] 2/3 Geminiによる解析とノイズ除去を開始します...")
        enriched_packages: List[KnowledgePackage] = []

        # コレクターから上がってきた各パッケージ（README、Issues、Releasesなど）を順次解析
        for raw_pkg in raw_packages:
            logger.info("[Workflow] 解析中: %s (Source: %s)", raw_pkg.title, raw_pkg.metadata.source)
            
            # Geminiを呼び出して、Summary/Capabilities/Constraints を補完
            enriched_pkg = await self.terminal_engine.analyze_package(raw_pkg)
            enriched_packages.append(enriched_pkg)

        logger.info("[Workflow] 解析・クレンジング完了。")

        # ── 3. 記憶・蓄積フェーズ (4. Embedding作成 / 5. DB管理) ──
        logger.info("[Workflow] 3/3 ChromaDBへの蓄積（ベクトル化）を実行中...")
        try:
            self.knowledge_searcher.add_packages(enriched_packages)
            logger.info("[Workflow] ChromaDBへの保存が正常に完了しました。")
        except Exception as e:
            logger.error("[Workflow] DBへの永続化フェーズでエラーが発生しました: %s", e)
            return False

        logger.info("==================================================")
        logger.info("[Workflow] パイプラインが正常に一気貫通しました！ 🎉")
        logger.info("==================================================")
        return True


async def main():
    """コマンドライン引数のパースと実行"""
    parser = argparse.ArgumentParser(description="OFA 知識インジェクション・パイプライン")
    parser.add_argument("--target", "-t", required=True, help="収集対象 (例: 'ratatui-org/ratatui' または Qiitaの記事ID)")
    parser.add_argument("--source", "-s", choices=["github", "qiita"], help="明示的なデータソースの指定")
    
    args = parser.parse_args()

    workflow = IngestionWorkflow()
    success = await workflow.execute(target=args.target, source=args.source)
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    # Windows環境等でのイベントループの互換性ケアを含めて実行
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())