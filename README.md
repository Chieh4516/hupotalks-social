# hupotalks-social

琥珀漫談（YouTube @hupotalks）社群貼文圖卡。只放已定案要公開的圖卡，供 Threads／Instagram／Facebook API 發文時讀取公開網址。

## 自動發文（GitHub Actions）

- `.github/workflows/social.yml` 每 15 分鐘跑一次 `python social_post.py due`：發 `social_queue.json` 裡 approved、時間已到、還沒發過的 IG、Threads 貼文，發完把 `social_post_state.json`、`social_post_log.jsonl` 提交回來（發過不重發）。
- FB 粉專不在這裡發：已用 Meta 平台排程。
- 權杖放在 repo 的 Secrets（`META_PAGE_TOKEN`、`THREADS_TOKEN`），不在檔案裡。Threads 權杖 60 天到期，到期前要在 Secrets 更新。
- 手動測試：Actions → 社群發文 → Run workflow，參數填 `test e01_p1 ig`（只建容器、不發布）。

## YouTube 來源留言（GitHub Actions，2026-11-08）

- `.github/workflows/yt_comment.yml` 每 15 分鐘跑一次 `python yt_comment.py due`：`yt_comment_queue.json` 裡的影片到了公開時間、真的變成公開、還沒留言，就把 `yt_comments/eNN.txt` 發成來源留言（私人影片不能留言）。發之前會先找頻道自己有沒有發過同一則，有就不重發；紀錄在 `yt_comment_state.json`。
- 發完開一則 Issue，列出要在 YouTube Studio 手動做的事（API 不能置頂）；做完關掉 Issue。公開時間過 3 小時還不是公開、或留言失敗，工作流程會失敗、GitHub 寄信通知。
- 授權放在 Secret `YT_TOKEN_JSON`（Mac 的 yt_token.json 整份）。Mac 重新授權 YouTube 後，要把新的 yt_token.json 貼回這個 Secret。
- 手動檢查：Actions → YouTube 來源留言 → Run workflow，參數 `check`（只讀，列出每集公開狀態）。
