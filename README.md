# Dvia Concepts auto-poster

Claude makes the 3 daily ads and writes them into a schedule file (`queue.json` in the
"Dvia Daily Ads" Google Drive folder). This bot runs on GitHub every 15 minutes, posts
whatever is due to **Facebook (Page)**, **Instagram (Reels)** and **YouTube Shorts**, and
records the result in `log/posted.json`, so nothing is ever posted twice.

Everything here is free. A platform you have not set up yet is simply skipped.

---

## Step 1: Put the bot on GitHub (10 min)

1. On github.com, click **+ → New repository**. Name: `dvia-poster`. Choose **Public**
   (your keys stay hidden in Secrets, and it lets Claude read the posting log), then **Create**.
2. Click **uploading an existing file**. Unzip `dvia-poster.zip` on your computer and drag
   **everything inside the folder** (including the `.github` folder) into the page.
   Click **Commit changes**.
   - If the `.github` folder doesn't upload (hidden folders can be skipped), click
     **Add file → Create new file**, type the name `.github/workflows/post.yml`, paste the
     contents of that file, and commit.
3. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `QUEUE_URL` = `https://drive.google.com/uc?export=download&id=1-moOn-9IxgNaMzbGgVLkKKmFnoMDpVvm`
4. In the same page open the **Variables** tab → **New repository variable**:
   `DRY_RUN` = `1` (test mode: the bot logs what it *would* post). Delete it when everything works.
5. Open the **Actions** tab and enable workflows if asked.

## Step 2: Facebook Page + Instagram (about 30 min, no Meta review needed)

You need: a Facebook **Page** for Dvia Concepts, and an Instagram **Business or Creator**
account linked to that Page (Instagram app → Settings → Account type / Linked accounts).

1. Go to **developers.facebook.com → My Apps → Create App**. Choose the use case
   that manages a Page / "Other" → type **Business**. Name it "Dvia Poster".
   Keep it in **Development mode** (you are the admin, so it can post to your own Page
   and Instagram without App Review).
2. Add the products **Facebook Login for Business** (or Facebook Login) and **Instagram Graph API**
   if the dashboard offers them.
3. Open **Tools → Graph API Explorer**. Pick your app, choose **User token**, and add these
   permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish`, `business_management`.
   Click **Generate Access Token** and allow everything for your Page and Instagram.
4. Make it long-lived: open **Tools → Access Token Debugger**, paste the token, click
   **Debug → Extend Access Token**, and copy the new token.
5. Back in Graph API Explorer, paste the long-lived token and run `GET me/accounts`.
   In the result, find your Page: copy its **id** → secret `FB_PAGE_ID`, and its
   **access_token** → secret `FB_PAGE_TOKEN` (this Page token does not expire).
6. Run `GET <your page id>?fields=instagram_business_account` and copy the id inside
   → secret `IG_USER_ID`.

## Step 3: YouTube Shorts (about 30 min, then Google's review)

1. Go to **console.cloud.google.com** → create a project "Dvia Poster".
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen**: User type **External**, app name
   "Dvia Poster", your email. Add yourself as a test user. Then click **Publish app**
   (status "In production"). Without this, the login stops working after 7 days.
   An "unverified app" warning on the login screen is fine for your own use.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → type
   **Web application**. Under *Authorized redirect URIs* add
   `https://developers.google.com/oauthplayground`. Copy the **Client ID** → secret
   `YT_CLIENT_ID` and **Client secret** → secret `YT_CLIENT_SECRET`.
5. Open **developers.google.com/oauthplayground**. Click the gear icon (top right) → tick
   **Use your own OAuth credentials** → paste the client ID and secret.
6. In the left box type `https://www.googleapis.com/auth/youtube.upload` → **Authorize APIs**
   → sign in with the Google account that owns the YouTube channel → allow.
7. Click **Exchange authorization code for tokens** and copy the **Refresh token**
   → secret `YT_REFRESH_TOKEN`.
8. Until Google reviews the project, uploads arrive as **private** videos. Apply with the
   **YouTube API Services Audit and Quota Extension Form** (search that name). Say the
   app uploads your own company's short ads to your own channel, 3 a day.

## Step 4: Test, then go live

1. **Actions → Post due ads → Run workflow**. With `DRY_RUN=1` it only writes the log.
   Check `log/posted.json`.
2. Delete the `DRY_RUN` variable. From then on the bot posts each ad at its time.
3. Tell Claude the repository name (e.g. `yourname/dvia-poster`) so the daily task can
   check the log and tell you if any post failed.

## Notes
- If a post fails it is retried on the next runs (max 3 tries), and never posted more than
  6 hours after its slot.
- GitHub may run scheduled jobs a few minutes late; that's normal.
- Never share the token values with anyone. To revoke: remove the app in Facebook settings
  / Google account permissions and delete the secrets.
- TikTok is not included yet: it needs TikTok's app audit. Metricool keeps posting TikTok meanwhile.
