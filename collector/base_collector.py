"""
collectors/base_collector.py
─────────────────────────────
すべてのデータ収集プラグイン（GitHub, Qiita, HuggingFace, ...）が
継承する基底クラス。

責務の分割:
    - 子クラス (_fetch_logic):
        対象API/サイトから「生データ(dict)」を取得することだけに専念する。
        ネットワークエラーや一時的な失敗は気にせず、例外は素通りさせてよい。

    - 子クラス (_build_package): [任意でオーバーライド]
        生データを KnowledgePackage に変換するロジック。
        各プラットフォームでフィールドの組み立て方が異なるため、
        デフォルト実装はあるが、通常は子クラス側で上書きする想定。

    - 親クラス (collect):
        リトライ、レートリミット待機、例外の分類・ロギング、
        Pydanticバリデーション結果の確認までを一括して担当する。
        manager.py からはこの collect() のみを呼べばよい。

戻り値の方針:
    collect() は常に Optional[KnowledgePackage] を返す。
    失敗時は None を返し、原因はログと例外チェーンに残す
    （manager.py 側で「件数カウント」や「失敗対象のリトライキュー」
    などの集約処理をしやすくするため、例外を上に伝播させない）。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx
from pydantic import ValidationError

from schemas.knowledge_package import KnowledgePackage, KnowledgeMeta

logger = logging.getLogger("OFA.Collector")


# ────────────────────────────────────────────────────────
# カスタム例外階層
# ────────────────────────────────────────────────────────
class CollectorError(Exception):
    """すべてのコレクター関連エラーの基底クラス。"""


class TransientError(CollectorError):
    """
    一時的な失敗（ネットワーク断、タイムアウト、5xx等）。
    リトライによって回復が見込めるエラーに使う。
    """


class RateLimitError(CollectorError):
    """
    レートリミット(429)に達した場合のエラー。
    Retry-After ヘッダーがあれば保持し、無ければ既定の待機時間を使う。
    """

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class FatalCollectorError(CollectorError):
    """
    リトライしても回復しない致命的エラー（401認証エラー、404等）。
    検知したら即座にリトライを中断する。
    """


class DataValidationError(CollectorError):
    """
    _fetch_logic は成功したが、KnowledgePackageへの変換・
    Pydanticバリデーションに失敗した場合のエラー。
    """


# ────────────────────────────────────────────────────────
# 基底コレクタークラス
# ────────────────────────────────────────────────────────
class BaseCollector(ABC):
    """
    すべてのデータ収集プラグインの基底クラス（非同期版）。

    使い方（子クラス側）:
        class RepositoryCollector(BaseCollector):
            @property
            def name(self) -> str:
                return "github_repository"

            async def _fetch_logic(self, target: str) -> Dict[str, Any]:
                # 生データ取得のみ。KnowledgePackageへの組み立てはしない。
                return await self.request_json(f"https://api.github.com/repos/{target}")

            def _build_package(self, target: str, raw: Dict[str, Any]) -> KnowledgePackage:
                # 生データ -> KnowledgePackage への変換（必要な分だけ上書き）
                return KnowledgePackage(...)
    """

    #: レートリミット(429)時の固定待機秒数
    RATE_LIMIT_WAIT_SECONDS: float = 60.0

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 10.0):
        """
        Args:
            client: 複数コレクターで共有する httpx.AsyncClient。
                    manager.py が一つ生成して各コレクターに注入するのが想定パターン
                    （コネクションプールを再利用でき、close漏れも防げる）。
                    渡されなかった場合はこのインスタンス専用のクライアントを生成する。
            timeout: 自前でクライアントを生成する場合のデフォルトタイムアウト(秒)。
        """
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    # ── 子クラスで実装必須 ──────────────────────────────
    @property
    @abstractmethod
    def name(self) -> str:
        """コレクターの識別名（例: 'github_repository'）。ログ・エラーメッセージに使う。"""
        raise NotImplementedError

    @abstractmethod
    async def _fetch_logic(self, target: str) -> Dict[str, Any]:
        """
        [子クラスで実装] 各APIやスクレイピングの純粋な取得ロジック。

        - ここでは生データ(dict)を返すことだけを考えればよい。
        - エラーハンドリング・リトライは collect() が担当するため、
          httpx の例外や CollectorError 系の例外は握らずそのまま投げてよい。
        - レートリミットを検知した場合は RateLimitError を投げる
          （self.request_json はこれを自動で行う）。
        """
        raise NotImplementedError

    # ── 子クラスで任意にオーバーライド ──────────────────
    def _build_package(self, target: str, raw: Dict[str, Any]) -> KnowledgePackage:
        """
        [デフォルト実装あり / 通常は子クラスでオーバーライド]
        生データ(dict)から KnowledgePackage を組み立てる。

        デフォルトでは raw の中身を素朴にマッピングするだけなので、
        各プラットフォーム特有のフィールド（star数、タグ抽出など）は
        子クラス側で上書きして実装することを推奨する。
        """
        return KnowledgePackage(
            id=f"{self.name}_{target}",
            title=str(raw.get("title", target)),
            summary=str(raw.get("summary", "")),
            raw_content=str(raw.get("raw_content", "")),
            capabilities=list(raw.get("capabilities", [])),
            constraints=list(raw.get("constraints", [])),
            metadata=KnowledgeMeta(
                source=self.name,
                url=raw.get("url"),
                tags=list(raw.get("tags", [])),
                raw_stats=dict(raw.get("raw_stats", {})),
            ),
        )

    # ── 共通エントリポイント ────────────────────────────
    async def collect(
        self,
        target: str,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ) -> Optional[KnowledgePackage]:
        """
        [共通メソッド] データ収集の実行エントリポイント。
        manager.py からは基本的にこのメソッドだけを呼べばよい。

        フロー:
            1. _fetch_logic() で生データ取得（リトライ・レートリミット対応）
            2. _build_package() で KnowledgePackage に変換
            3. 失敗時は None を返す（例外は外に投げない）

        Args:
            target: 収集対象の識別子（例: 'owner/repo', 記事ID など）
            max_retries: 一時的エラー時の最大リトライ回数
            backoff_factor: 指数バックオフの係数（backoff_factor ** retries 秒待機）

        Returns:
            成功時: KnowledgePackage インスタンス
            失敗時: None
        """
        logger.info("[%s] 収集を開始します。対象: %s", self.name, target)

        raw = await self._fetch_with_retry(target, max_retries, backoff_factor)
        if raw is None:
            # _fetch_with_retry 側で既にエラーログ済み
            return None

        try:
            package = self._build_package(target, raw)
        except ValidationError as e:
            logger.error(
                "[%s] KnowledgePackageへの変換に失敗しました（バリデーションエラー）。対象: %s, エラー: %s",
                self.name, target, e,
            )
            return None
        except Exception as e:
            logger.error(
                "[%s] KnowledgePackageへの変換中に予期しないエラーが発生しました。対象: %s, エラー: %s",
                self.name, target, e,
            )
            return None

        logger.info("[%s] 収集に成功しました。対象: %s", self.name, target)
        return package

    # ── 内部: リトライ・レートリミット制御 ───────────────
    async def _fetch_with_retry(
        self,
        target: str,
        max_retries: int,
        backoff_factor: float,
    ) -> Optional[Dict[str, Any]]:
        """_fetch_logic を呼び出し、エラー種別に応じてリトライ制御を行う。"""
        retries = 0

        while retries <= max_retries:
            try:
                return await self._fetch_logic(target)

            except RateLimitError as e:
                wait = e.retry_after or self.RATE_LIMIT_WAIT_SECONDS
                logger.warning(
                    "[%s] レートリミットに達しました。%s秒待機します... (%s)",
                    self.name, wait, e,
                )
                await asyncio.sleep(wait)
                retries += 1
                continue

            except FatalCollectorError as e:
                # リトライ不可能なエラーは即座に中断
                logger.error("[%s] 致命的エラーのため中断します。対象: %s, エラー: %s", self.name, target, e)
                return None

            except (TransientError, httpx.TransportError, httpx.TimeoutException) as e:
                retries += 1
                if retries > max_retries:
                    logger.error(
                        "[%s] 最大リトライ回数(%d)に達しました。対象: %s, エラー: %s",
                        self.name, max_retries, target, e,
                    )
                    return None
                sleep_time = backoff_factor ** retries
                logger.warning(
                    "[%s] 一時的なエラーを検知。%.1f秒後にリトライします (%d/%d)... エラー: %s",
                    self.name, sleep_time, retries, max_retries, e,
                )
                await asyncio.sleep(sleep_time)

            except CollectorError as e:
                # 想定済みだが上記以外のCollectorError（DataValidationErrorなど）
                logger.error("[%s] コレクターエラーが発生しました。対象: %s, エラー: %s", self.name, target, e)
                return None

            except Exception as e:
                # 想定外のエラーも一時的エラーとして扱い、上限まではリトライする
                retries += 1
                if retries > max_retries:
                    logger.error(
                        "[%s] 想定外のエラーで最大リトライ回数に達しました。対象: %s, エラー: %s",
                        self.name, target, e,
                    )
                    return None
                sleep_time = backoff_factor ** retries
                logger.warning(
                    "[%s] 想定外のエラーを検知。%.1f秒後にリトライします (%d/%d)... エラー: %s",
                    self.name, sleep_time, retries, max_retries, e,
                )
                await asyncio.sleep(sleep_time)

        return None

    # ── 子クラスが使える共通HTTPユーティリティ ────────────
    async def request_json(
        self,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        [共通ユーティリティ] httpx.AsyncClient を使った安全なGETリクエスト。

        ステータスコードに応じて適切なカスタム例外に変換してから投げるため、
        子クラス側は素直に await するだけでよい
        （例外処理は親クラスの _fetch_with_retry に綺麗に流れる）。
        """
        try:
            response = await self._client.get(url, headers=headers, params=params)
        except httpx.TimeoutException as e:
            raise TransientError(f"タイムアウトしました: {url}") from e
        except httpx.TransportError as e:
            raise TransientError(f"接続エラーが発生しました: {url}") from e

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            retry_after = float(retry_after_header) if retry_after_header else None
            raise RateLimitError(f"レートリミットに達しました: {url}", retry_after=retry_after)

        if response.status_code in (401, 403, 404):
            raise FatalCollectorError(
                f"致命的なHTTPエラー (Status: {response.status_code}): {url}"
            )

        if response.status_code >= 500:
            raise TransientError(
                f"サーバーエラー (Status: {response.status_code}): {url}"
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise TransientError(f"HTTPエラー (Status: {response.status_code}): {url}") from e

        return response.json()

    async def aclose(self) -> None:
        """
        このコレクターが自前で生成したクライアントのみクローズする。
        manager.py から注入されたクライアントは manager.py 側の責任でクローズするため、
        ここでは閉じない（二重クローズ防止）。
        """
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "BaseCollector":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()