"""
agents/evaluator_agent.py
─────────────────────────────────
生成された成果物（コード、デザイン、テキスト等）の品質と制約遵守をチェックする評価AI。

役割（15アプリ構成の該当箇所）:
    - 13. 評価AI: 出力チェック・フィードバック生成

設計メモ:
    - Geminiの JSON出力モード を使用し、評価結果を構造化データとして取得。
    - 「パスしたか（is_passed）」だけでなく、「スコア（score）」や
      「具体的な修正ポイント（feedback_points）」を出力することで、
      14番の修正AI（修正エンジン）がピンポイントで自己修正できるように設計。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import google.generativeai as genai

logger = logging.getLogger("OFA.EvaluatorAgent")


class EvaluatorAgent:
    """生成された成果物が要件や制約を満たしているかを厳格に審査するエージェント。"""

    def __init__(self, model_name: str = "gemini-2.5-pro"):
        """
        Args:
            model_name: 複雑なロジックや制約の検証を行うため、
                        推論能力の高い Pro モデルを推奨。
        """
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("[EvaluatorAgent] GEMINI_API_KEY が設定されていません。")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

        # 評価結果のJSONスキーマ定義
        self.system_instruction = """
        あなたは厳格な品質管理エンジニア（QA）であり、コードや成果物のコードレビューを行うAIです。
        提出された「成果物（Artifact）」が、「ユーザーの要求」および「システム制約・技術制約（Constraints）」を
        完全に満たしているかを厳密にチェックし、以下のJSONフォーマットで出力してください。
        忖度や甘口の評価は一切不要です。バグや制約違反があれば厳しく指摘してください。

        【出力JSONスキーマ】
        {
            "is_passed": true または false (すべての必須要件・致命的な制約をクリアしていれば true),
            "score": 0 から 100 の数値 (品質スコア。100が完璧),
            "reason": "合格または不合格とした総合的な理由・根拠（1〜2文）",
            "feedback_points": [
                "修正すべき問題点、不足している機能、違反している制約などを具体的に箇条書きで記述（なければ空配列）"
            ]
        }
        """

    async def evaluate(
        self,
        artifact: str,
        constraints: List[str],
        user_requirement: str
    ) -> Dict[str, Any]:
        """
        成果物を評価する。

        Args:
            artifact: 生成された成果物（ソースコード、HTML、ドキュメントなど）
            constraints: 守るべき制約事項のリスト（KnowledgePackage等から抽出されたもの）
            user_requirement: ユーザーが元々要求していた内容

        Returns:
            Dict[str, Any]: スキーマに沿った評価結果の辞書
        """
        logger.info("[EvaluatorAgent] 成果物の評価を開始します...")

        # 制約事項を箇条書きテキストに整形
        constraints_text = "\n".join([f"- {c}" for c in constraints]) if constraints else "(制約事項は特に指定されていません)"

        # 評価用プロンプトの組み立て
        evaluation_prompt = f"""
        【元々のユーザー要求】
        {user_requirement}

        【厳守すべき制約事項 (Constraints)】
        {constraints_text}

        【提出された成果物 (Artifact)】
        {artifact}
        """

        try:
            # GeminiのJSON出力モードを呼び出し
            response = await self.model.generate_content_async(
                contents=[self.system_instruction, evaluation_prompt],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,  # 評価基準をブレさせないために極めて低い温度に設定
                )
            )

            result_dict: Dict[str, Any] = json.loads(response.text)
            
            logger.info(
                "[EvaluatorAgent] 評価完了。Passed: %s, Score: %d/100",
                result_dict.get("is_passed"),
                result_dict.get("score", 0)
            )
            return result_dict

        except Exception as e:
            logger.error("[EvaluatorAgent] 評価中にエラーが発生しました: %s", e)
            # 万が一エラーが起きた場合は安全側に倒して不合格にする
            return {
                "is_passed": False,
                "score": 0,
                "reason": f"Evaluation system error: {str(e)}",
                "feedback_points": ["システムエラーにより評価を完了できませんでした。"]
            }