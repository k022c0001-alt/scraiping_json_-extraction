"""
generators/html_agent.py
─────────────────────────────────
ユーザー要求と関連知識（Constraints）から、HTML構造を生成するAI。

役割（15アプリ構成の該当箇所）:
    - 10. HTML生成AI: HTML生成

設計メモ:
    - evaluator_agent.py / corrector_agent.py と同じ Gemini 呼び出しパターンを踏襲。
    - クリーニング処理（コードフェンス除去）はこのファイル内にローカル関数として実装。
      3箇所目（css_agent / js_agent）でも同じ処理が必要になった時点で
      generators/utils/code_cleaner.py への切り出しを検討する。
    - 出力は schemas.GeneratedCode に詰めて返す。raw_response も保持し、
      クリーニング前後の差分をデバッグできるようにしている。
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

import google.generativeai as genai

from generators.schemas import CodeLanguage, GeneratedCode

logger = logging.getLogger("OFA.HtmlAgent")


def _clean_code_block(raw_text: str) -> str:
    """
    LLM出力からコードフェンス（```html ... ```）を除去し、純粋なコードのみを返す。

    - フェンスが複数ある場合は最大のブロックを採用（説明文中の小さいコード片混入対策）。
    - フェンスが見つからない場合は生テキストをそのまま使う（フォールバック）。
    """
    pattern = r"```(?:\w+)?\s*\n(.*?)```"
    matches = re.findall(pattern, raw_text, re.DOTALL)

    if matches:
        code = max(matches, key=len)
    else:
        logger.warning("[HtmlAgent] コードフェンスが検出されませんでした。生テキストを使用します。")
        code = raw_text

    return code.strip()


class HtmlAgent:
    """ユーザー要求と制約からHTMLを生成するエージェント。"""

    def __init__(self, model_name: str = "gemini-2.5-pro"):
        """
        Args:
            model_name: マークアップ構造の妥当性判断が必要なため Pro モデルを推奨。
                        速度重視なら gemini-2.5-flash への差し替えも可能。
        """
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("[HtmlAgent] GEMINI_API_KEY が設定されていません。")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

        self.system_instruction = """
        あなたは経験豊富なフロントエンドエンジニアであり、セマンティックで
        アクセシビリティに配慮したHTMLを生成するAIです。

        【出力ルール】
        - 解説や挨拶（「以下がHTMLです」など）は一切不要です。
        - 完全なHTML構造を、マークダウンのコードブロック（```html）形式で出力してください。
        - id / class 名は、CSS生成AI・JS生成AIが参照することを前提に、
          意味の分かる명名（例: id="main-nav", class="card-list"）にしてください。
        - インラインの style 属性や <style> タグ、<script> タグは使用しないでください
          （CSSとJSはそれぞれ専用の生成AIが別途担当します）。
        - 指定された制約（Constraints）に技術的な縛りがある場合は必ず遵守してください。
        """

    async def generate(
        self,
        user_requirement: str,
        constraints: Optional[List[str]] = None,
        reference_snippets: Optional[str] = None,
    ) -> GeneratedCode:
        """
        ユーザー要求からHTMLを生成する。

        Args:
            user_requirement: ユーザーが要求している内容
            constraints: 守るべき技術制約（KnowledgePackage等から抽出されたもの）
            reference_snippets: Snippet Engine（App8）が抽出した参考知識（任意）

        Returns:
            GeneratedCode: クリーニング済みHTMLと生レスポンスを含むモデル
        """
        logger.info("[HtmlAgent] HTML生成を開始します...")

        constraints_text = (
            "\n".join([f"- {c}" for c in constraints]) if constraints else "(特になし)"
        )
        reference_text = reference_snippets or "(参考知識は特にありません)"

        generation_prompt = f"""
        【ユーザー要求】
        {user_requirement}

        【厳守すべき制約事項 (Constraints)】
        {constraints_text}

        【参考知識・関連スニペット】
        {reference_text}
        """

        try:
            response = await self.model.generate_content_async(
                contents=[self.system_instruction, generation_prompt],
                generation_config=genai.GenerationConfig(
                    temperature=0.3,  # 構造の一貫性を保ちつつ、ある程度の創造性も許容
                ),
            )

            raw_text = response.text
            cleaned_code = _clean_code_block(raw_text)

            logger.info("[HtmlAgent] HTML生成完了。文字数: %d", len(cleaned_code))

            return GeneratedCode(
                language=CodeLanguage.HTML,
                code=cleaned_code,
                raw_response=raw_text,
            )

        except Exception as e:
            logger.error("[HtmlAgent] 生成中にエラーが発生しました: %s", e)
            # 安全側に倒す: 空のHTMLを返し、評価AI側で不合格として弾かれるようにする
            return GeneratedCode(
                language=CodeLanguage.HTML,
                code="",
                raw_response=f"Generation system error: {str(e)}",
            )