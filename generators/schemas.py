"""
generators/schemas.py
─────────────────────────────────
App10〜14（HTML/CSS/JS生成AI、評価AI、修正AI）間でやり取りされる
データを型安全に扱うためのPydanticモデル群。

設計メモ:
    - GeneratedCode: 各生成AI（10〜12）の出力を統一フォーマットで保持。
    - EvaluationResult: evaluator_agent.py の JSON出力スキーマと完全対応。
    - CorrectionRequest: corrector_agent.py への入力をひとまとめにする。
    - WorkflowState: workflow_engine.py がループ全体で保持する状態。
      （LangGraphのState定義としてそのまま使うことを想定）
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class CodeLanguage(str, Enum):
    """生成・修正対象のコード種別。expected_langの検証等に使う。"""
    HTML = "html"
    CSS = "css"
    JS = "js"


class GeneratedCode(BaseModel):
    """
    各生成AI（App10〜12）の出力を表す統一フォーマット。
    クリーニング済みの純粋なコード文字列のみを保持する。
    """
    language: CodeLanguage
    code: str = Field(..., description="フェンス除去済みの純粋なソースコード")
    raw_response: Optional[str] = Field(
        default=None,
        description="デバッグ用。クリーニング前のLLM生レスポンスを保持（任意）"
    )


class EvaluationResult(BaseModel):
    """
    evaluator_agent.py の出力JSONスキーマと1対1対応するモデル。
    現状は素のdictで返しているが、workflow_engine.py側で
    型安全に扱いたい場合はこのモデルでパースする。
    """
    is_passed: bool
    score: int = Field(..., ge=0, le=100)
    reason: str
    feedback_points: List[str] = Field(default_factory=list)


class CorrectionRequest(BaseModel):
    """corrector_agent.fix_artifact() への入力をまとめたモデル（任意で利用）。"""
    artifact: str
    feedback_points: List[str]
    user_requirement: str
    current_retry: int = Field(..., ge=1)
    max_retries: int = Field(..., ge=1)


class WorkflowState(BaseModel):
    """
    workflow_engine.py（App10〜14のAIリレー＆ループ）が
    LangGraphのStateとして保持する想定のトップレベルモデル。

    1回のユーザー要求に対して、HTML/CSS/JSそれぞれが
    独立した評価・修正ループを持てるよう、コード種別ごとに状態を分離している。
    """
    user_requirement: str
    constraints: List[str] = Field(default_factory=list)

    html: Optional[GeneratedCode] = None
    css: Optional[GeneratedCode] = None
    js: Optional[GeneratedCode] = None

    latest_evaluation: Optional[EvaluationResult] = None
    current_retry: int = 0
    max_retries: int = 3

    is_completed: bool = False