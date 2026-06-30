　　　root/
│
├── api/
│   └── .env                          # [実装済み] APIキー等の環境変数
│
├── terminal_workflow.py               # [一部実装済 / 調整中] 統括ワークフローの入り口
│
├── schemas/
│   └── knowledge_package.py          # [設計提示済み] 共通JSONフォーマットの型定義(Pydantic)
│
├── collectors/                       # ─── 【収集層】プラグイン化されたコレクター群
│   ├── __init__.py
│   ├── base_collector.py             # [設計提示済み] コレクターの共通インターフェース(ABC)
│   ├── manager.py                    # [設計提示済み] 全コレクターを動的ロード・統括するマネージャー
│   │
│   ├── github/                       # 各コンポーネントに細分化
│   │   ├── __init__.py               # [設計提示済み] サブコレクターを束ねるマスター
│   │   ├── repository.py             # [設計提示済み] Star, Fork, 言語, ライセンス等の基本情報
│   │   ├── issue.py                  # [これから実装] 直近の課題、議論の収集
│   │   ├── release.py                # [これから実装] バージョン履歴、Changelog
│   │   └── commit.py                 # [これから実装] 直近のコード変更アクティビティ
│   │
│   ├── qiita/
│   │   ├── __init__.py
│   │   └── article.py                # [これから実装] 技術記事、タグ、LGTM数の収集
│   │
│   └── huggingface/ / zenn/ / npm/   # [これから実装] 今後必要に応じて追加するプラグイン
│
├── analyzers/                        # ─── 【解析層】
│   └── terminal_engine.py            # [一部実装済] 収集データを読み込み、Geminiで解析するエンジン
│
├── core/                             # ─── 【思考・検索層】エージェントの「脳」
│   ├── knowledge_searcher.py         # [設計提示済み] SQLite/VectorDB/JSONのハイブリッド横断検索
│   └── ofa_planner.py                # [設計提示済み] 知識を元にHandlerの実行計画(Plan)を立てる司令塔
│
├── data/                             # ─── 【記憶層】データベース
│   └── agent_brain.db                # [自動生成] 結晶化したKnowledgePackageが蓄積されるSQLite
│
├── knowledge/                        # ─── 【静的ルール層】挙動を微調整する固定制約JSON
│   ├── html/                         # (button.json, navbar.json など) [これから実装]
│   ├── css/                          # (flex.json, grid.json など) [これから実装]
│   └── terminal/                     # (git.json, docker.json など) [これから実装]
│
└── ts_layer/                         # ─── 【実行・具現化層】TypeScript ＆ フロントエンド
    ├── handlers/
    │   └── handler.ts                # [設計提示済み] PythonからのPlan(JSON)を解釈・実行するディスパッチャー
    │
    └── frontend/                     # ─── 【UI出力層】
        └── components/               # [これから実装] React / HTML / SVG / Terminalを可視化するUI　　　　　　　　　　　　　　　　次のステップに向けた連携
これでQiitaコレクターの実装が完了しました。Claudeさんに引き継ぐ際は、以下のポイントを伝えていただくとスムーズです。
この collectors/qiita/article.py を配置する。
collectors/manager.py の上部のコメントアウトされている from collectors.qiita.article import ArticleCollector を有効化する。
同じく manager.py の COLLECTOR_REGISTRY 辞書の "qiita" のリストに ArticleCollector を追加する。
これで、マネージャーの collect_by_source({"github": "owner/repo", "qiita": "記事ID"}) を使って、GitHubとQiitaを並行して一気に情報収集できるようになります！