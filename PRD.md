# Product Requirements Document: News Scraper

Status: v1 built and running locally; server deployment pending.

## 1. Summary

An internal web app that gathers news once a day from a team-defined list of websites and topics, and stores it as an organised daily briefing that people read in the browser and that other systems can pick up later through exports.

## 2. Problem

The team needs to follow news from many sources every day. Checking sites by hand is slow and inconsistent, some content (LinkedIn posts, YouTube videos) can't be collected automatically, and there is no single organised place to read or hand the results to another tool.

## 3. Goals

1. Collect fresh articles every day with no manual work, trusted sources first.
2. Let non-technical team members manage sources and topics from the UI.
3. Keep all results organised by day and by source, readable and exportable.
4. Capture non-scrapable material (pasted posts, YouTube videos) in the same briefing.
5. Run on the company's own server, behind a login.

## 4. Non-goals

- Summarising or rewriting articles with AI (explicitly declined, including for videos).
- Scraping sites that forbid it or require login (for example LinkedIn).
- Crawling the entire web; discovery is through a search API on defined topics.
- Per-user accounts, roles or audit trails (one shared login is enough for now).
- Rendering JavaScript-only sites (a headless browser was deferred).
- Speech-to-text for videos without captions.

## 5. Users

A small internal team (editors, researchers, marketers) who read the briefing daily and add sources. One shared login. A downstream system or person may later consume the exports.

## 6. User stories

- As a team member, I paste or upload a list of URLs and mark which are priority, then start scraping.
- As a team member, I add topics and the app searches for them.
- As a reader, I open today's briefing, see top stories first, and read any story in full without leaving the app.
- As a reader, I switch to earlier days and search all stories.
- As a team member, I paste a LinkedIn post or a YouTube link so it joins the briefing.
- As an admin, I see run history, failures and counts.
- As a downstream consumer, I fetch a day as JSON or CSV.
- As an admin, I pause or delete a source without touching code.

## 7. Functional requirements

### 7.1 Sources and topics
- FR-1 Add URLs by paste or `.txt`/`.csv` upload; add `https://` automatically; ignore duplicates.
- FR-2 Each source has priority and enabled flags; each topic has an enabled flag.
- FR-3 Toggle, re-prioritise and delete from the Sources page, showing items produced per source.

### 7.2 Scraping
- FR-4 A run processes enabled priority sources first, then other sources, then topic searches.
- FR-5 For each source use its RSS/Atom feed if present, else same-site links with substantial anchor text (up to 15 per source).
- FR-6 For each topic fetch up to 10 news results from DuckDuckGo.
- FR-7 Extract title, date, body text, excerpt and pictures; discard pages with under 200 characters of text and section/home pages without a date.
- FR-8 Never save the same link twice across days.
- FR-9 One failing article must not stop the run.
- FR-10 Only one run at a time; progress is visible live; a stale run is cleared after a restart.
- FR-11 Runs start automatically daily (default 07:00, configurable time zone) and on demand.

### 7.3 Pasted posts and videos
- FR-12 "Paste a post": text (20 to 20,000 characters), optional link, author and date.
- FR-13 YouTube links (up to 5 at once): title, channel, description, date, thumbnail, embedded player.
- FR-14 Timestamped transcript from YouTube captions, shown as readable paragraphs with clickable timestamps.
- FR-15 Graph/slide screenshots from the video file (uploaded with the link, or downloaded only if explicitly enabled), with an optional AI filter and caption when an API key is configured.
- FR-16 Video jobs run one at a time and can be reprocessed.

### 7.4 Reading and exporting
- FR-17 Briefing page: day navigation, spotlight of top stories (max two per source), board grouped by source, search palette.
- FR-18 Full-window reader with headline, picture or video, justified article text, transcript and gallery; headline stays pinned while scrolling.
- FR-19 History page: 14-week activity grid and run table.
- FR-20 Export a day as JSON or CSV.
- FR-21 Pictures are stored locally and served only to logged-in users.

### 7.5 Access and security
- FR-22 All pages require login except `/login` and `/healthz`; failed logins are rate limited.
- FR-23 CSRF token on every POST.
- FR-24 Security headers including a Content-Security-Policy.
- FR-25 Fetching only public hosts (redirects re-checked), respect `robots.txt`, cap response sizes.
- FR-26 Secrets live in environment files outside the repository.

## 8. Non-functional requirements

- **Hosting:** Hostinger VPS (4 GB RAM) with CloudPanel/Nginx, systemd service, memory-capped.
- **Performance:** a normal run (tens of sources, a handful of topics) completes unattended; one request per second per fetch to be polite.
- **Reliability:** restart-safe; daily backups of database and pictures kept 14 days.
- **Storage:** SQLite by default; Postgres possible via `DATABASE_URL`.
- **Maintainability:** tests in `tests/`, deployment guide in `DEPLOY.md`.
- **Compliance:** respect site terms and robots; YouTube downloading is off by default.
- **Time zone:** Asia/Kolkata for the schedule.

## 9. Data model (summary)

- **Source:** url, priority, enabled
- **Topic:** name, enabled
- **Item:** run date, sequence, type (priority, search, manual, video), source, topic, title, link (unique), published, excerpt, text, image(s), video id, transcript, screenshots, processing state and note
- **Run:** start/finish, status, phase, message, totals, items added

## 10. Success criteria (proposed)

- The daily run completes without manual action on at least 95% of days.
- A new source or topic goes from entry to first stored article in a single run.
- Less than 5% of saved items are non-articles (section pages, empty pages).
- Team members can add sources, read and export without developer help.
- No secrets are present in the repository.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Sites block scrapers or load content with JavaScript | Respect robots, polite delay; add a headless browser later if needed |
| YouTube blocks transcript requests from the VPS | May need a proxy; transcripts fail gracefully with a note |
| Legal/terms issues (LinkedIn, YouTube downloads) | LinkedIn is paste-only; downloads off by default |
| Leaked keys | Secrets kept out of git and out of OneDrive-synced paths; rotate after deployment |
| Single worker limits scale | Fine for a small team; revisit if usage grows |
| Search API limits or changes (free ddgs) | Swap in a paid search API behind the same function |

## 12. Open questions

1. Final hosting: confirm the VPS deployment and domain set up.
2. Does the YouTube transcript fetch work from the VPS's IP?
3. Should a paid search API replace free DuckDuckGo for better coverage?
4. Is a headless browser needed for the user's actual URL list?
5. Will a downstream system consume the exports, and in what format or schedule?
6. Should the shared login become per-user accounts later?

## 13. Milestones

1. Done: scraper, database, UI, login, images, YouTube, tests.
2. Next: deploy to the VPS and run for a week with the real URL and topic lists.
3. Then: tune extraction on the real sources, decide on search API and headless browser.
4. Later: per-user accounts, downstream integration.
