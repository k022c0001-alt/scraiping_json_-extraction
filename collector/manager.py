"""
collectors/manager.py
─────────────────────────────────
全コレクター（GitHub, Qiita, HuggingFace, ...）を統括するマネージャー。

責務:
    1. コレクタークラスの「明示登録レジストリ」を保持する
       （動的import/プラグイン自動検出はせず、このファイルに手で書き加える方式）。
    2. 指定された target に対し、対象ソースに属する全コレクターを
       asyncio.gather で並行実行し、収集結果(KnowledgePackage)をリストで返す。
    3. httpx.AsyncClient をマネージャー側で一元生成し、全コレクターに注入する
       （コネクションプールの再利用、close漏れ防止）。

責務外（呼び出し側の責任）:
    - 取得した KnowledgePackage のリストを SQLite (data/agent_brain.db) に
      保存する処理。manager.py はあくまで「収集してリストを返す」までを担う。

新しいコレクターを追加する手順:
    1. collectors/<source>/<xxx>.py に BaseCollector のサブクラスを実装する
    2. このファイルの先頭で import する
    3. COLLECTOR_REGISTRY の該当する source のリストにクラスを追加する
    （これだけで manager.collect_all() から自動的に並行実行対象になる）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Type

import httpx

from collectors.base_collector import BaseCollector
from schemas.knowledge_package import KnowledgePackage

# ── 実装済みコレクターの import ──────────────────────────
from collectors.github.repository import RepositoryCollector
from collectors.github.issue import IssueCollector
from collectors.github.release import ReleaseCollector

# ── これから実装予定のコレクター（実装でき次第、import と登録を追加） ──
# from collectors.github.commit import CommitCollector
# from collectors.qiita.article import ArticleCollector
# from collectors.huggingface.model import ModelCollector
# from collectors.zenn.article import ZennArticleCollector
# from collectors.npm.package import NpmPackageCollector

logger = logging.getLogger("OFA.Manager")


# ────────────────────────────────────────────────────────
# 明示登録レジストリ
# ────────────────────────────────────────────────────────
# source名 -> そのsourceに属するコレクタークラスのリスト。
# 同じsource配下のコレクターは「同じ形式のtarget」を受け取る前提
# （例: github配下はすべて "owner/repo" 形式のtargetを受け取る）。
COLLECTOR_REGISTRY: Dict[str, List[Type[BaseCollector]]] = {
    "github": [
        RepositoryCollector,
        IssueCollector,
        ReleaseCollector,
        # CommitCollector,  # commit.py 実装後にここへ追加
    ],
    # "qiita": [
    #     ArticleCollector,
    # ],
    # "huggingface": [
    #     ModelCollector,
    # ],
}


class CollectionManager:
    """
    登録された全コレクターを統括し、targetに対する並行収集を行うマネージャー。

    使い方:
        async with CollectionManager() as manager:
            packages = await manager.collect_all("ratatui-org/ratatui", sources=["github"])
            # packages: List[KnowledgePackage]（取得失敗分は自動的に除外される）
    """

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)

    def _instantiate_collectors(self, sources: List[str]) -> List[BaseCollector]:
        """
        指定された source名のリストから、登録済みコレクタークラスを
        インスタンス化したリストを作る。未登録のsource名は警告を出して無視する。
        """
        collectors: List[BaseCollector] = []
        for source in sources:
            collector_classes = COLLECTOR_REGISTRY.get(source)
            if collector_classes is None:
                logger.warning(
                    "[Manager] 未登録のsourceが指定されました: '%s'（COLLECTOR_REGISTRYを確認してください）",
                    source,
                )
                continue
            for cls in collector_classes:
                collectors.append(cls(client=self._client))
        return collectors

    async def collect_all(
        self,
        target: str,
        sources: Optional[List[str]] = None,
    ) -> List[KnowledgePackage]:
        """
        指定された target に対し、対象ソースに属する全コレクターを並行実行する。

        Args:
            target: 収集対象の識別子。
                    （現時点では github系のコレクターのみ登録されており、
                    すべて "owner/repo" 形式を期待する。
                    sourceごとにtarget形式が異なる場合は collect_by_source を使う）
            sources: 収集対象のsource名リスト。Noneの場合はレジストリの全sourceを対象にする。

        Returns:
            収集に成功した KnowledgePackage のリスト。
            一部のコレクターが失敗（Noneを返した）場合も、
            成功した分だけを返し例外は投げない。
        """
        target_sources = sources if sources is not None else list(COLLECTOR_REGISTRY.keys())
        collectors = self._instantiate_collectors(target_sources)

        if not collectors:
            logger.warning("[Manager] 実行対象のコレクターが0件でした。sources=%s", target_sources)
            return []

        logger.info(
            "[Manager] %d件のコレクターを並行実行します。対象: %s, sources=%s",
            len(collectors), target, target_sources,
        )

        results = await asyncio.gather(
            *(collector.collect(target) for collector in collectors),
            return_exceptions=True,
        )

        packages: List[KnowledgePackage] = []
        for collector, result in zip(collectors, results):
            if isinstance(result, BaseException):
                # collect()自体は内部で例外を握る設計だが、万一の想定外を二重に保護する
                logger.error(
                    "[Manager] コレクター '%s' で想定外の例外が発生しました: %s",
                    collector.name, result,
                )
                continue
            if result is None:
                logger.warning("[Manager] コレクター '%s' は収集に失敗しました（Noneが返却）。", collector.name)
                continue
            packages.append(result)

        logger.info(
            "[Manager] 収集完了。成功: %d件 / 実行: %d件",
            len(packages), len(collectors),
        )
        return packages

    async def collect_by_source(
        self,
        targets: Dict[str, str],
    ) -> List[KnowledgePackage]:
        """
        source毎にtargetの形式が異なる場合に使うメソッド。
        例: {"github": "ratatui-org/ratatui", "qiita": "Rust TUI"}

        Args:
            targets: { source名: そのsourceに渡すtarget文字列 } の辞書。

        Returns:
            収集に成功した KnowledgePackage のリスト。
        """
        collector_target_pairs: List[tuple[BaseCollector, str]] = []
        for source, target in targets.items():
            collector_classes = COLLECTOR_REGISTRY.get(source)
            if collector_classes is None:
                logger.warning("[Manager] 未登録のsourceが指定されました: '%s'", source)
                continue
            for cls in collector_classes:
                collector_target_pairs.append((cls(client=self._client), target))

        if not collector_target_pairs:
            logger.warning("[Manager] 実行対象のコレクターが0件でした。targets=%s", targets)
            return []

        logger.info("[Manager] %d件のコレクターを並行実行します（source別target）。", len(collector_target_pairs))

        results = await asyncio.gather(
            *(collector.collect(target) for collector, target in collector_target_pairs),
            return_exceptions=True,
        )

        packages: List[KnowledgePackage] = []
        for (collector, _target), result in zip(collector_target_pairs, results):
            if isinstance(result, BaseException):
                logger.error("[Manager] コレクター '%s' で想定外の例外が発生しました: %s", collector.name, result)
                continue
            if result is None:
                logger.warning("[Manager] コレクター '%s' は収集に失敗しました（Noneが返却）。", collector.name)
                continue
            packages.append(result)

        logger.info("[Manager] 収集完了。成功: %d件 / 実行: %d件", len(packages), len(collector_target_pairs))
        return packages

    async def aclose(self) -> None:
        """マネージャーが生成した共有httpxクライアントをクローズする。"""
        await self._client.aclose()

    async def __aenter__(self) -> "CollectionManager":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()