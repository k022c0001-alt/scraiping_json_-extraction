"""
core/knowledge_searcher.py
─────────────────────────────────
知識パッケージの永続化・検索エンジン。
ハイブリッド検索（セマンティック検索 + 従来のDB検索）を実現する。

設計:
    1. ChromaDB: ベクトル化された知識の高速セマンティック検索
       - raw_content や summary をテキスト埋め込みしてベクトル化
       - コサイン類似度による検索

    2. SQLite: 構造化メタデータの厳密な検索
       - ソース別フィルタリング（github, qiita, etc.）
       - タグベース検索
       - 処理状態（is_analyzed, is_embedded）の追跡

    3. ハイブリッド検索:
       - 複合条件（テキスト + フィルター）での検索
       - スコア加重（ベクトル類似度 + 統計情報）

使用例:
    searcher = KnowledgeSearcher()
    
    # 1. パッケージをDBに追加
    packages = [pkg1, pkg2, pkg3]
    searcher.add_packages(packages)
    
    # 2. セマンティック検索
    results = searcher.search_by_text("Rust TUI framework", limit=5)
    
    # 3. タグベース検索
    results = searcher.search_by_tag("rust")
    
    # 4. ソース別検索
    github_packages = searcher.search_by_source("github_repository")
    
    # 5. 複合検索
    results = searcher.hybrid_search(
        query="TUI rendering",
        filters={"source": "github_repository", "tags": ["rust"]},
        limit=10
    )
    
    # 6. パッケージを取得
    pkg = searcher.get_package_by_id("github_ratatui-org_ratatui")
    
    # 7. 統計情報
    stats = searcher.get_stats()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings

from schemas.knowledge_package import KnowledgePackage, KnowledgeSource, KnowledgeMeta

logger = logging.getLogger("OFA.KnowledgeSearcher")


# ────────────────────────────────────────────────────────
# ChromaDB クライアントのセットアップ
# ────────────────────────────────────────────────────────
class ChromaDBClient:
    """ChromaDB クライアントのラッパー（永続化対応）。"""

    def __init__(self, persist_dir: str = "data/chromadb"):
        """
        Args:
            persist_dir: ChromaDB のデータ永続化ディレクトリ
        """
        Path(persist_dir).mkdir(parents=True, exist_ok=True)

        # ChromaDB の永続化設定
        settings = Settings(
            chroma_db_impl="duckdb+parquet",  # 永続化バックエンド
            persist_directory=persist_dir,
            anonymized_telemetry=False,
        )

        # クライアント初期化
        self.client = chromadb.Client(settings)
        self.collection_name = "knowledge_packages"
        
        # コレクション取得（存在しなければ作成）
        try:
            self.collection = self.client.get_collection(name=self.collection_name)
            logger.info(f"[ChromaDB] 既存コレクション '{self.collection_name}' を取得しました")
        except Exception:
            self.collection = self.client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"[ChromaDB] 新規コレクション '{self.collection_name}' を作成しました")

    def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: Dict[str, Any],
    ) -> None:
        """ドキュメントをコレクションに追加。"""
        self.collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )
        logger.debug(f"[ChromaDB] ドキュメント追加: {doc_id}")

    def update_document(
        self,
        doc_id: str,
        text: str,
        metadata: Dict[str, Any],
    ) -> None:
        """ドキュメントを更新。"""
        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )
        logger.debug(f"[ChromaDB] ドキュメント更新: {doc_id}")

    def search(
        self,
        query_text: str,
        limit: int = 10,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        セマンティック検索を実行。

        Args:
            query_text: 検索クエリ
            limit: 返す結果の最大件数
            where: フィルター条件（ChromaDB の WHERE 形式）

        Returns:
            [
                {
                    "id": "...",
                    "text": "...",
                    "metadata": {...},
                    "distance": 0.25,  # コサイン距離（小さいほど類似）
                },
                ...
            ]
        """
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=limit,
                where=where,
            )

            # ChromaDB の返り値を標準化
            output = []
            if results and results["ids"] and len(results["ids"]) > 0:
                for idx, doc_id in enumerate(results["ids"][0]):
                    output.append({
                        "id": doc_id,
                        "text": results["documents"][0][idx] if results["documents"] else "",
                        "metadata": results["metadatas"][0][idx] if results["metadatas"] else {},
                        "distance": results["distances"][0][idx] if results["distances"] else 1.0,
                    })
            
            logger.debug(f"[ChromaDB] 検索完了: '{query_text}' → {len(output)} 件")
            return output

        except Exception as e:
            logger.error(f"[ChromaDB] 検索エラー: {e}")
            return []

    def get_count(self) -> int:
        """コレクション内のドキュメント数。"""
        return self.collection.count()

    def delete_document(self, doc_id: str) -> None:
        """ドキュメントを削除。"""
        self.collection.delete(ids=[doc_id])
        logger.debug(f"[ChromaDB] ドキュメント削除: {doc_id}")

    def clear(self) -> None:
        """コレクション内のすべてのドキュメントを削除。"""
        # 全件取得して削除（ChromaDBに delete_all がない場合の代替）
        try:
            all_docs = self.collection.get()
            if all_docs and all_docs["ids"]:
                self.collection.delete(ids=all_docs["ids"])
                logger.info(f"[ChromaDB] コレクションをクリアしました")
        except Exception as e:
            logger.error(f"[ChromaDB] クリアエラー: {e}")


# ────────────────────────────────────────────────────────
# SQLite メタデータストア
# ────────────────────────────────────────────────────────
class SQLiteMetadataStore:
    """SQLite で構造化メタデータと処理状態を管理。"""

    def __init__(self, db_path: str = "data/agent_brain.db"):
        """
        Args:
            db_path: SQLite DB ファイルパス
        """
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """テーブルを初期化。"""
        with self.conn:
            # メインテーブル
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_packages (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT,
                    source TEXT NOT NULL,
                    url TEXT,
                    is_analyzed BOOLEAN DEFAULT 0,
                    is_embedded BOOLEAN DEFAULT 0,
                    content_length INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    package_json TEXT  -- 全体の JSON 保存用（復元時に使う）
                )
            """)

            # タグテーブル（normalize）
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    package_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    FOREIGN KEY(package_id) REFERENCES knowledge_packages(id),
                    UNIQUE(package_id, tag)
                )
            """)

            # 統計情報テーブル
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_stats (
                    id TEXT PRIMARY KEY,
                    package_id TEXT NOT NULL,
                    stat_key TEXT NOT NULL,
                    stat_value TEXT,
                    FOREIGN KEY(package_id) REFERENCES knowledge_packages(id)
                )
            """)

            # インデックス作成
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON knowledge_packages(source)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_is_analyzed ON knowledge_packages(is_analyzed)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_is_embedded ON knowledge_packages(is_embedded)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tag ON knowledge_tags(tag)")

        logger.info(f"[SQLite] DB 初期化完了: {self.db_path}")

    def insert_package(self, package: KnowledgePackage) -> None:
        """パッケージをDBに挿入。"""
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO knowledge_packages
                (id, title, summary, source, url, is_analyzed, is_embedded, 
                 content_length, version, package_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                package.id,
                package.title,
                package.summary,
                package.metadata.source.value,
                package.metadata.url,
                int(package.is_analyzed),
                int(package.is_embedded),
                package.content_length(),
                package.version,
                package.to_json(),
            ))

            # タグを登録
            self.conn.executemany(
                "INSERT OR IGNORE INTO knowledge_tags (package_id, tag) VALUES (?, ?)",
                [(package.id, tag) for tag in package.metadata.tags]
            )

            # 統計情報を登録
            for key, value in package.metadata.raw_stats.items():
                self.conn.execute("""
                    INSERT OR REPLACE INTO knowledge_stats (id, package_id, stat_key, stat_value)
                    VALUES (?, ?, ?, ?)
                """, (f"{package.id}_{key}", package.id, key, str(value)))

        logger.debug(f"[SQLite] パッケージ挿入: {package.id}")

    def get_package_by_id(self, package_id: str) -> Optional[KnowledgePackage]:
        """ID でパッケージを取得。"""
        cursor = self.conn.execute(
            "SELECT package_json FROM knowledge_packages WHERE id = ?",
            (package_id,)
        )
        row = cursor.fetchone()
        if row:
            try:
                return KnowledgePackage.from_json(row[0])
            except Exception as e:
                logger.error(f"[SQLite] パッケージ復元エラー {package_id}: {e}")
                return None
        return None

    def search_by_source(self, source: str, limit: int = 100) -> List[KnowledgePackage]:
        """ソース別に検索。"""
        cursor = self.conn.execute(
            "SELECT package_json FROM knowledge_packages WHERE source = ? LIMIT ?",
            (source, limit)
        )
        packages = []
        for row in cursor.fetchall():
            try:
                packages.append(KnowledgePackage.from_json(row[0]))
            except Exception as e:
                logger.error(f"[SQLite] パッケージ復元エラー: {e}")
        return packages

    def search_by_tag(self, tag: str, limit: int = 100) -> List[KnowledgePackage]:
        """タグで検索。"""
        cursor = self.conn.execute("""
            SELECT DISTINCT kp.package_json
            FROM knowledge_packages kp
            JOIN knowledge_tags kt ON kp.id = kt.package_id
            WHERE kt.tag = ?
            LIMIT ?
        """, (tag, limit))
        packages = []
        for row in cursor.fetchall():
            try:
                packages.append(KnowledgePackage.from_json(row[0]))
            except Exception as e:
                logger.error(f"[SQLite] パッケージ復元エラー: {e}")
        return packages

    def search_by_analyzed_status(self, is_analyzed: bool, limit: int = 100) -> List[KnowledgePackage]:
        """解析状態で検索。"""
        cursor = self.conn.execute(
            "SELECT package_json FROM knowledge_packages WHERE is_analyzed = ? LIMIT ?",
            (int(is_analyzed), limit)
        )
        packages = []
        for row in cursor.fetchall():
            try:
                packages.append(KnowledgePackage.from_json(row[0]))
            except Exception as e:
                logger.error(f"[SQLite] パッケージ復元エラー: {e}")
        return packages

    def update_analyzed_status(self, package_id: str, is_analyzed: bool) -> None:
        """解析ステータスを更新。"""
        with self.conn:
            self.conn.execute(
                "UPDATE knowledge_packages SET is_analyzed = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(is_analyzed), package_id)
            )
        logger.debug(f"[SQLite] 解析ステータス更新: {package_id} → {is_analyzed}")

    def update_embedded_status(self, package_id: str, is_embedded: bool) -> None:
        """ベクトル化ステータスを更新。"""
        with self.conn:
            self.conn.execute(
                "UPDATE knowledge_packages SET is_embedded = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(is_embedded), package_id)
            )
        logger.debug(f"[SQLite] ベクトル化ステータス更新: {package_id} → {is_embedded}")

    def delete_package(self, package_id: str) -> None:
        """パッケージを削除。"""
        with self.conn:
            self.conn.execute("DELETE FROM knowledge_tags WHERE package_id = ?", (package_id,))
            self.conn.execute("DELETE FROM knowledge_stats WHERE package_id = ?", (package_id,))
            self.conn.execute("DELETE FROM knowledge_packages WHERE id = ?", (package_id,))
        logger.debug(f"[SQLite] パッケージ削除: {package_id}")

    def get_stats(self) -> Dict[str, Any]:
        """全体的な統計情報を取得。"""
        cursor = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_analyzed THEN 1 ELSE 0 END) as analyzed,
                SUM(CASE WHEN is_embedded THEN 1 ELSE 0 END) as embedded,
                SUM(content_length) as total_content_length
            FROM knowledge_packages
        """)
        row = cursor.fetchone()
        
        # ソース別の件数
        source_cursor = self.conn.execute("""
            SELECT source, COUNT(*) as count
            FROM knowledge_packages
            GROUP BY source
        """)
        sources = {row[0]: row[1] for row in source_cursor.fetchall()}

        return {
            "total_packages": row[0] or 0,
            "analyzed": row[1] or 0,
            "embedded": row[2] or 0,
            "total_content_length": row[3] or 0,
            "by_source": sources,
        }

    def close(self) -> None:
        """DB接続を閉じる。"""
        self.conn.close()
        logger.debug("[SQLite] DB接続を閉じました")


# ────────────────────────────────────────────────────────
# メインの KnowledgeSearcher クラス
# ────────────────────────────────────────────────────────
class KnowledgeSearcher:
    """
    ハイブリッド検索エンジン。
    ChromaDB + SQLite を組み合わせて、セマンティック検索と構造化検索を実現。
    """

    def __init__(
        self,
        chromadb_dir: str = "data/chromadb",
        sqlite_path: str = "data/agent_brain.db",
    ):
        """
        Args:
            chromadb_dir: ChromaDB の永続化ディレクトリ
            sqlite_path: SQLite DB ファイルパス
        """
        self.chromadb = ChromaDBClient(persist_dir=chromadb_dir)
        self.sqlite = SQLiteMetadataStore(db_path=sqlite_path)
        logger.info("[KnowledgeSearcher] 初期化完了")

    # ────────────────────────────────────
    # 追加・更新操作
    # ────────────────────────────────────

    def add_package(self, package: KnowledgePackage) -> None:
        """単一パッケージを追加。"""
        self.add_packages([package])

    def add_packages(self, packages: List[KnowledgePackage]) -> None:
        """複数パッケージを一括追加。"""
        if not packages:
            logger.warning("[KnowledgeSearcher] 追加するパッケージが0件です")
            return

        for package in packages:
            try:
                # SQLite にメタデータを保存
                self.sqlite.insert_package(package)

                # ChromaDB にテキストをベクトル化して保存
                # raw_content と summary を組み合わせて埋め込み
                text_for_embedding = f"{package.title}\n{package.summary}\n{package.raw_content}"
                
                # メタデータは string に変換（ChromaDB の要件）
                metadata = {
                    "source": package.metadata.source.value,
                    "url": package.metadata.url or "",
                    "tags": ",".join(package.metadata.tags),
                    "is_analyzed": str(package.is_analyzed),
                }

                self.chromadb.add_document(
                    doc_id=package.id,
                    text=text_for_embedding,
                    metadata=metadata,
                )

                # ベクトル化完了をマーク
                package.mark_embedded()
                self.sqlite.update_embedded_status(package.id, True)

                logger.info(f"[KnowledgeSearcher] パッケージ追加: {package.id}")

            except Exception as e:
                logger.error(f"[KnowledgeSearcher] パッケージ追加エラー {package.id}: {e}")
                package.add_error(str(e))

    def update_package(self, package: KnowledgePackage) -> None:
        """パッケージを更新（Gemini解析後等）。"""
        try:
            self.sqlite.insert_package(package)

            # ChromaDB も更新
            text_for_embedding = f"{package.title}\n{package.summary}\n{package.raw_content}"
            metadata = {
                "source": package.metadata.source.value,
                "url": package.metadata.url or "",
                "tags": ",".join(package.metadata.tags),
                "is_analyzed": str(package.is_analyzed),
            }

            self.chromadb.update_document(
                doc_id=package.id,
                text=text_for_embedding,
                metadata=metadata,
            )

            logger.info(f"[KnowledgeSearcher] パッケージ更新: {package.id}")

        except Exception as e:
            logger.error(f"[KnowledgeSearcher] パッケージ更新エラー {package.id}: {e}")

    # ────────────────────────────────────
    # 検索操作
    # ────────────────────────────────────

    def search_by_text(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        セマンティック検索（テキストクエリ）。

        Args:
            query: 検索クエリ（例: "Rust TUI framework"）
            limit: 返す結果の最大件数

        Returns:
            [
                {
                    "package": KnowledgePackage,
                    "score": 0.85,  # 類似度スコア（高いほどよい）
                    "distance": 0.15,  # コサイン距離（ChromaDB）
                },
                ...
            ]
        """
        results = self.chromadb.search(query_text=query, limit=limit)
        
        output = []
        for result in results:
            package = self.sqlite.get_package_by_id(result["id"])
            if package:
                # distance を score に変換（距離が小さい = スコアが高い）
                score = max(0.0, 1.0 - result["distance"])
                output.append({
                    "package": package,
                    "score": score,
                    "distance": result["distance"],
                })

        logger.info(f"[KnowledgeSearcher] テキスト検索: '{query}' → {len(output)} 件")
        return output

    def search_by_source(self, source: str, limit: int = 100) -> List[KnowledgePackage]:
        """
        ソース別検索。

        Args:
            source: ソース名（例: "github_repository", "qiita_article"）
            limit: 返す結果の最大件数
        """
        packages = self.sqlite.search_by_source(source, limit=limit)
        logger.info(f"[KnowledgeSearcher] ソース検索: {source} → {len(packages)} 件")
        return packages

    def search_by_tag(self, tag: str, limit: int = 100) -> List[KnowledgePackage]:
        """
        タグベース検索。

        Args:
            tag: タグ（例: "rust", "python"）
            limit: 返す結果の最大件数
        """
        packages = self.sqlite.search_by_tag(tag, limit=limit)
        logger.info(f"[KnowledgeSearcher] タグ検索: {tag} → {len(packages)} 件")
        return packages

    def hybrid_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        複合検索（テキスト + フィルター）。

        Args:
            query: 検索クエリ
            filters: フィルター条件
                例: {
                    "source": "github_repository",
                    "tags": ["rust", "tui"],  # いずれかを含む
                    "is_analyzed": True,
                }
            limit: 返す結果の最大件数

        Returns:
            [
                {
                    "package": KnowledgePackage,
                    "score": 0.85,
                },
                ...
            ]
        """
        # ChromaDB のフィルター条件を構築
        where_clause = None
        if filters:
            conditions = []
            
            if "source" in filters:
                conditions.append({"source": {"$eq": filters["source"]}})
            
            if "is_analyzed" in filters:
                conditions.append({"is_analyzed": {"$eq": str(filters["is_analyzed"])}})

            if conditions:
                # 複数条件は AND で結合
                where_clause = {"$and": conditions} if len(conditions) > 1 else conditions[0]

        # ChromaDB で検索
        results = self.chromadb.search(
            query_text=query,
            limit=limit,
            where=where_clause,
        )

        # SQLite での追加フィルター（タグなど）
        output = []
        for result in results:
            package = self.sqlite.get_package_by_id(result["id"])
            if not package:
                continue

            # タグフィルター
            if "tags" in filters and filters["tags"]:
                tag_match = any(t in package.metadata.tags for t in filters["tags"])
                if not tag_match:
                    continue

            score = max(0.0, 1.0 - result["distance"])
            output.append({
                "package": package,
                "score": score,
                "distance": result["distance"],
            })

        logger.info(f"[KnowledgeSearcher] 複合検索: '{query}' (filters={filters}) → {len(output)} 件")
        return output

    # ────────────────────────────────────
    # 取得操作
    # ────────────────────────────────────

    def get_package_by_id(self, package_id: str) -> Optional[KnowledgePackage]:
        """ID でパッケージを取得。"""
        return self.sqlite.get_package_by_id(package_id)

    def get_analyzed_packages(self, limit: int = 100) -> List[KnowledgePackage]:
        """解析済みパッケージをすべて取得。"""
        return self.sqlite.search_by_analyzed_status(is_analyzed=True, limit=limit)

    def get_unanalyzed_packages(self, limit: int = 100) -> List[KnowledgePackage]:
        """未解析パッケージをすべて取得。"""
        return self.sqlite.search_by_analyzed_status(is_analyzed=False, limit=limit)

    # ────────────────────────────────────
    # 状態管理
    # ────────────────────────────────────

    def mark_as_analyzed(self, package_id: str) -> None:
        """パッケージを解析済みとしてマーク。"""
        self.sqlite.update_analyzed_status(package_id, True)

    def mark_as_embedded(self, package_id: str) -> None:
        """パッケージをベクトル化済みとしてマーク。"""
        self.sqlite.update_embedded_status(package_id, True)

    # ────────────────────────────────────
    # 削除操作
    # ────────────────────────────────────

    def delete_package(self, package_id: str) -> None:
        """パッケージを削除（SQLite + ChromaDB から）。"""
        self.sqlite.delete_package(package_id)
        self.chromadb.delete_document(package_id)
        logger.info(f"[KnowledgeSearcher] パッケージ削除: {package_id}")

    def clear_all(self) -> None:
        """すべてのパッケージを削除（注意：復旧不可）。"""
        logger.warning("[KnowledgeSearcher] すべてのパッケージをクリアしています...")
        self.chromadb.clear()
        # SQLite は再初期化
        self.sqlite.close()
        import os
        if os.path.exists(self.sqlite.db_path):
            os.remove(self.sqlite.db_path)
        self.sqlite = SQLiteMetadataStore(self.sqlite.db_path)
        logger.warning("[KnowledgeSearcher] クリア完了")

    # ────────────────────────────────────
    # 統計・監視
    # ────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """統計情報を取得。"""
        sqlite_stats = self.sqlite.get_stats()
        chromadb_count = self.chromadb.get_count()

        return {
            "sqlite": sqlite_stats,
            "chromadb": {
                "total_documents": chromadb_count,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }

    def print_stats(self) -> None:
        """統計情報をログに出力。"""
        stats = self.get_stats()
        logger.info("=" * 60)
        logger.info("[KnowledgeSearcher] 統計情報")
        logger.info("=" * 60)
        logger.info(f"総パッケージ数: {stats['sqlite']['total_packages']}")
        logger.info(f"解析済み: {stats['sqlite']['analyzed']}")
        logger.info(f"ベクトル化済み: {stats['sqlite']['embedded']}")
        logger.info(f"総コンテンツ長: {stats['sqlite']['total_content_length']} 文字")
        logger.info(f"ソース別:")
        for source, count in stats['sqlite']['by_source'].items():
            logger.info(f"  - {source}: {count} 件")
        logger.info("=" * 60)

    def close(self) -> None:
        """リソースを解放。"""
        self.sqlite.close()
        logger.info("[KnowledgeSearcher] リソース解放完了")
