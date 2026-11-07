# hupotalks-social

琥珀漫談（YouTube @hupotalks）社群貼文圖卡。只放已定案要公開的圖卡，供 Threads／Instagram／Facebook API 發文時讀取公開網址。

## 自動發文（GitHub Actions）

- `.github/workflows/social.yml` 每 15 分鐘跑一次 `python social_post.py due`：發 `social_queue.json` 裡 approved、時間已到、還沒發過的 IG、Threads 貼文，發完把 `social_post_state.json`、`social_post_log.jsonl` 提交回來（發過不重發）。
- FB 粉專不在這裡發：已用 Meta 平台排程。
- 權杖放在 repo 的 Secrets（`META_PAGE_TOKEN`、`THREADS_TOKEN`），不在檔案裡。Threads 權杖 60 天到期，到期前要在 Secrets 更新。
- 手動測試：Actions → 社群發文 → Run workflow，參數填 `test e01_p1 ig`（只建容器、不發布）。
