"""
agents/corrector_agent.py
─────────────────────────────────
評価AI（EvaluatorAgent）からのフィードバックを基に、成果物を自律修正する修正AI。

役割（15アプリ構成の該当箇所）:
    - 14. 修正AI: 自己修正・リファインエンジン

設計メモ:
    - 13番（評価AI）が指摘した `feedback_points` を基に、成果物のバグや制約違反を修正する。
    - システム全体の無限ループを防止するため、呼び出し側（ワークフロー）が管理する
      「修正ループの上限（max_retries）」と「現在の試行回数（current_retry）」を感化。
      上限が近づくにつれて、プロンプトに強力な警告（根本原因の解消指示）を動的に注入する。
"""

from __future__ import annotations

import logging
import os
from typing import List

import google.generativeai as genai

logger = logging.getLogger("OFA.CorrectorAgent")


class CorrectorAgent:
    """不合格となった成果物を、フィードバックと修正限界を考慮しながら自律修正するエージェント。"""

    def __init__(self, model_name: str = "gemini-2.5-pro"):
        """
        Args:
            model_name: コードの再構造化など、高度な書き換え能力が必要なため
                        Pro モデルを推奨。
        """
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("[CorrectorAgent] GEMINI_API_KEY が設定されていません。")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

        # 修正AIとしての基本システム指示
        self.base_system_instruction = """
        あなたは極めて優秀なシニアソフトウェアエンジニアであり、AIエージェントの自己修正システム（Self-Correction）の中核です。
        提供された「元の成果物」に対し、QA（評価AI）からの「フィードバック（指摘事項）」をすべて完全に解消した、
        クオリティの高い修正版の成果物を出力してください。
        
        【出力ルール】
        - 解説や余計な挨拶（「はい、修正しました」など）は一切不要です。
        - 成果物がソースコードやHTMLなどの場合、マークダウンのコードブロック（```python や ```html）形式で、
          修正後の完全なコードだけを出力してください。部分的な差分ではなく、そのまま置き換え可能な全体を出力すること。
        """

    async def fix_artifact(
        self,
        artifact: str,
        feedback_points: List[str],
        user_requirement: str,
        current_retry: int,
        max_retries: int
    ) -> str:
        """
        フィードバックとループの上限回数を考慮して、成果物を修正する。

        Args:
            artifact: 修正対象の元の成果物（ソースコードやHTMLなど）
            feedback_points: 評価AIから突きつけられた問題点・修正箇所のリスト
            user_requirement: ユーザーが元々要求していた内容
            current_retry: 現在の修正試行回数（1から始まる）
            max_retries: ワークフローが許可している最大リトライ（修正）上限回数

        Returns:
            str: 修正された新しい成果物
        """
        logger.info(
            "[CorrectorAgent] 修正処理を開始します。ループ状況: (%d / %d)", 
            current_retry, max_retries
        )

        # ── 💡「修正ループの上限」に絡む、動的なプロンプト制御 ──
        urgency_instruction = ""
        
        if current_retry >= max_retries:
            # 既に上限に達している（これが本当に最後のチャンス）場合
            urgency_instruction = f"""
            ⚠️【極めて重要な警告：これが最後の修正チャンスです】
            現在、システムが許容する最大修正回数（{max_retries}回中、{current_retry}回目）に達しました。
            これが最終試行であり、これ以上のリトライは許されません。
            小手先の場当たり的な修正や、変更によって新たなバグを生むような修正は絶対に避けてください。
            指摘されているフィードバックの「根本原因」がどこにあるのかを深く洞察し、
            最も安全で、最も確実に要求を満たす完成された成果物を提出してください。
            """
        elif max_retries - current_retry == 1:
            # 残りあと1回で上限に達する場合（イエローカード状態）
            urgency_instruction = f"""
            💡【注意：残りの修正回数が残りわずか（あと1回）です】
            修正ループの制限（上限 {max_retries} 回中、現在 {current_retry} 回目）が近づいています。
            前回の修正方針そのものが間違っている（または視野が狭くなっている）可能性があります。
            必要であれば、実装のアプローチや設計をドラスティックに根本から見直し、視野を広く持って修正してください Tweed。
            """
        else:
            # まだ回数に余裕がある場合
            urgency_instruction = f"""
            ℹ️【現在の修正ステータス】
            修正ループの上限までまだ余裕があります（{max_retries}回中、{current_retry}回目）。
            焦らず、指摘されたフィードバックを1つずつ確実に潰してください。
            """

        # フィードバックのテキスト化
        feedback_text = "\n".join([f"- {f}" for f in feedback_points])

        # 修正用プロンプトの組み立て
        correction_prompt = f"""
        {urgency_instruction}

        【元々のユーザー要求】
        {user_requirement}

        【QA（評価AI）からの指摘事項・フィードバック】
        {feedback_text}

        【修正対象の元の成果物】
        {artifact}
        """

        try:
            # 修正の実行
            response = await self.model.generate_content_async(
                contents=[self.base_system_instruction, correction_prompt],
                generation_config=genai.GenerationConfig(
                    temperature=0.2,  # 修正の創造性と正確性のバランスを取るため、評価AI(0.1)よりは少しだけ上げる
                )
            )

            logger.info("[CorrectorAgent] 修正版の出力を生成しました。")
            return response.text

        except Exception as e:
            logger.error("[CorrectorAgent] 修正処理中にエラーが発生しました: %s", e)
            # エラー時は安全のために元の成果物をそのまま返してループを壊さないようにする
            return artifact