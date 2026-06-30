"""
generators/css_agent.py
─────────────────────────────────
HTML生成AI（App10）が出力したHTML構造を参照し、それに対応するCSSを生成するAI。

役割（15アプリ構成の該当箇所）:
    - 11. CSS生成AI: CSS生成

設計メモ:
    - html_agent.py が出力した GeneratedCode(HTML) をそのまま入力として受け取り、
      id / class 名を勝手に作らず、既存のHTML構造に対して「後付け」する形でCSSを書く。
    - クリーニング処理（コードフェンス除去）は html_agent.py と全く同じロジック。
      これで2箇所目。js_agent.py が3箇所目になった時点で
      generators/utils/code_cleaner.py への切り出しを行う。
    - 出力は schemas.GeneratedCode に詰めて返す。
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

import google.generativeai as genai

from generators.schemas import CodeLanguage, GeneratedCode

logger = logging.getLogger("OFA.CssAgent")


def _clean_code_block(raw_text: str) -> str:
    """
    LLM出力からコードフェンス（```css ... ```）を除去し、純粋なコードのみを返す。

    - フェンスが複数ある場合は最大のブロックを採用（説明文中の小さいコード片混入対策）。
    - フェンスが見つからない場合は生テキストをそのまま使う（フォールバック）。
    """
    pattern = r"```(?:\w+)?\s*\n(.*?)```"
    matches = re.findall(pattern, raw_text, re.DOTALL)

    if matches:
        code = max(matches, key=len)
    else:
        logger.warning("[CssAgent] コードフェンスが検出されませんでした。生テキストを使用します。")
        code = raw_text

    return code.strip()


class CssAgent:
    """既存のHTML構造に対応するCSSを生成するエージェント。"""

    def __init__(self, model_name: str = "gemini-2.5-pro"):
        """
        Args:
            model_name: レイアウト崩れのない正確なセレクタ指定が必要なため Pro モデルを推奨。
                        速度重視なら gemini-2.5-flash への差し替えも可能。
        """
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("[CssAgent] GEMINI_API_KEY が設定されていません。")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

        self.system_instruction = """
        あなたは経験豊富なフロントエンドエンジニアであり、モダンで美しいCSSを
        生成するAIです。

        【出力ルール】
        - 解説や挨拶（「以下がCSSです」など）は一切不要です。
        - 完全なCSSを、マークダウンのコードブロック（```css）形式で出力してください。
        - 必ず、与えられた「対象のHTML」に実際に存在する id / class セレクタのみを
          使用してください。HTMLに存在しないセレクタを新たに作り出してはいけません。
        - HTML側のid/classを変更・追加することはできません。あくまでCSS側だけで
          スタイリングを完結させてください。
        - インラインJSに依存するようなスタイル（例: data-* 属性によるJS制御前提のクラス）が
          HTML側に見つかった場合は、それを壊さないようにスタイリングしてください。
        - 指定された制約（Constraints）に技術的な縛りがある場合は必ず遵守してください
          （例: 特定のCSSフレームワーク禁止、レスポンシブ対応必須、など）。
        """

    async def generate(
        self,
        html_code: str,
        user_requirement: str,
        constraints: Optional[List[str]] = None,
        reference_snippets: Optional[str] = None,
    ) -> GeneratedCode:
        """
        対象のHTMLに対応するCSSを生成する。

        Args:
            html_code: html_agent.py が生成した GeneratedCode.code（クリーニング済みHTML）
            user_requirement: ユーザーが要求している内容（デザインの雰囲気・トーン等）
            constraints: 守るべき技術制約（KnowledgePackage等から抽出されたもの）
            reference_snippets: Snippet Engine（App8）が抽出した参考知識（任意）

        Returns:
            GeneratedCode: クリーニング済みCSSと生レスポンスを含むモデル
        """
        logger.info("[CssAgent] CSS生成を開始します...")

        constraints_text = (
            "\n".join([f"- {c}" for c in constraints]) if constraints else "(特になし)"
        )
        reference_text = reference_snippets or "(参考知識は特にありません)"

        generation_prompt = f"""
        【ユーザー要求（デザインの方向性）】
        {user_requirement}

        【厳守すべき制約事項 (Constraints)】
        {constraints_text}

        【参考知識・関連スニペット】
        {reference_text}

        【対象のHTML（このセレクタのみ使用可）】
        {html_code}
        """

        try:
            response = await self.model.generate_content_async(
                contents=[self.system_instruction, generation_prompt],
                generation_config=genai.GenerationConfig(
                    temperature=0.4,  # デザインの創造性を少し優先しつつ、構造逸脱は防ぐ
                ),
            )

            raw_text = response.text
            cleaned_code = _clean_code_block(raw_text)

            logger.info("[CssAgent] CSS生成完了。文字数: %d", len(cleaned_code))

            return GeneratedCode(
                language=CodeLanguage.CSS,
                code=cleaned_code,
                raw_response=raw_text,
            )

        except Exception as e:
            logger.error("[CssAgent] 生成中にエラーが発生しました: %s", e)
            # 安全側に倒す: 空のCSSを返し、評価AI側で不合格として弾かれるようにする
            return GeneratedCode(
                language=CodeLanguage.CSS,
                code="",
                raw_response=f"Generation system error: {str(e)}",
            )