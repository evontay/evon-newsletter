# MosAIc Pulse — Claude Code Build Prompt

Paste everything below this line directly into your Claude Code terminal session.

---

Build me a Python script called `mosaic_pulse.py` that automates a weekly industry intelligence digest for a UX/DesignOps team. Here are the full requirements:

## What it does

Collects articles from RSS feeds, filters them for relevance using Claude Haiku, synthesises a structured digest using Claude Sonnet, and sends it to an email address. Runs as a single script I can execute manually or schedule via cron.

## Environment variables required

Read these from a `.env` file using `python-dotenv`:
- `ANTHROPIC_API_KEY` — Anthropic API key
- `SMTP_HOST` — smtp.gmail.com
- `SMTP_PORT` — 465
- `SMTP_USER` — sender Gmail address
- `SMTP_PASSWORD` — Gmail App Password
- `TO_EMAIL` — recipient work email address

## RSS Sources

Store sources in a separate `sources.py` file as a Python list of dicts with `url` and `name` keys. Populate it with these initial sources grouped by stream:

Craft & Practice: UX Collective (https://uxdesign.cc/feed), A List Apart (https://alistapart.com/articles/rss), Nielsen Norman Group (https://www.nngroup.com/feed/rss/), UX Magazine (https://uxmag.com/feed), Rosenfeld Media (https://rosenfeldmedia.com/feed/)

Tools & AI Fluency: Figma Blog (https://www.figma.com/blog/rss.xml), The Rundown AI (https://therundown.ai/feed), One Useful Thing — Mollick (https://www.oneusefulthing.org/feed), Dovetail Blog (https://dovetail.com/blog/rss/), OpenAI Blog (https://openai.com/blog/rss.xml), Anthropic Blog (https://www.anthropic.com/rss.xml), AI Snake Oil (https://www.aisnakeoil.com/feed), Simon Willison (https://simonwillison.net/atom/everything/)

Design Leadership: Julie Zhuo (https://joulee.medium.com/feed), Mind the Product (https://www.mindtheproduct.com/feed/), Mule Design (https://www.muledesign.com/blog/rss), Matthew Strom (https://matthewstrom.com/feed.xml), Peter Merholz Blog (https://www.petermerholz.com/rss/), Peter Merholz Newsletter (https://www.petermerholz.com/newsletter/rss/), Proof of Concept (https://www.proofofconcept.pub/feed), Eflowers Substack (https://eflowers.substack.com/feed), Medium Design Leadership (https://medium.com/feed/tag/design-leadership)

Horizon Watch: Benedict Evans (https://www.ben-evans.com/benedictevans/rss.xml), Import AI (https://jack-clark.net/feed/), Pragmatic Engineer (https://newsletter.pragmaticengineer.com/feed), Exponential View (https://www.exponentialview.co/feed), MIT Tech Review (https://www.technologyreview.com/feed/)

YouTube: NNg YouTube (https://www.youtube.com/feeds/videos.xml?channel_id=UCW3VsBfpxXB8BXPWI0JOIfQ), Figma YouTube (https://www.youtube.com/feeds/videos.xml?channel_id=UCkVKBqCG-GSsxmE6mMfGpWw), Google Design (https://www.youtube.com/feeds/videos.xml?channel_id=UC-b3c7kxa5vU-bnmaROgvog)

Jobs / Skills Market: Academy UX Jobs (https://blog.academyux.com/tag/jobs/rss/), Indeed UX Designer SG (https://www.indeed.com/rss?q=UX+Designer&l=Singapore), Indeed DesignOps SG (https://www.indeed.com/rss?q=DesignOps&l=Singapore), Indeed AI UX SG (https://www.indeed.com/rss?q=AI+UX&l=Singapore)

Adding a new source should require only adding one line to `sources.py`. No other file should need editing.

## Step 1 — Collect

Fetch all RSS feeds in parallel using `concurrent.futures.ThreadPoolExecutor`. Use `feedparser` to parse each feed. For each entry extract: title, url (from link field), author, pubDate (parse to datetime), snippet (from summary or content, strip HTML tags).

Timeout each feed fetch at 10 seconds. Skip feeds that fail silently — log the failure to console but continue.

## Step 2 — Filter

Apply these filters before sending to Claude:
- Only items published within the last 30 days
- Must have both title and url
- Deduplicate by url
- Per-domain cap: max 2 items per domain EXCEPT for these priority low-volume sources which are uncapped: petermerholz.com, proofofconcept.pub, eflowers.substack.com, muledesign.com, matthewstrom.com, joulee.medium.com, ben-evans.com, oneusefulthing.org, aisnakeoil.com
- Separate job postings (urls containing indeed.com, academyux.com, linkedin.com/jobs) into a separate list — do NOT send these to Haiku
- Cap remaining items at 20 before sending to Haiku
- Truncate title to 100 chars and snippet to 100 chars before building the scoring prompt

## Step 3 — Haiku Relevance Filter

Send the 20 non-job items to Claude Haiku (claude-haiku-4-5-20251001) for relevance scoring.

System prompt: "Relevance filter for a UX/DesignOps team. Score each item 1-3. Priority: (1) AI-ready UX workflows, (2) design leadership, (3) UX research methods, (4) DesignOps maturity. YouTube URLs get +1 bonus capped at 3. Assign stream: craft | tools | leadership | horizon. Return ONLY valid JSON array: [{\"index\":1,\"score\":3,\"stream\":\"tools\",\"reason\":\"one sentence\"}]"

Parse the JSON response. If parsing fails, fall back to using the first 10 items unscored with stream "craft". Sort by score descending. Take top 10. Append up to 2 job items at the end (pre-labelled with stream "jobs"). Final list passed to Sonnet = top 10 editorial + up to 2 jobs = max 12 items.

## Step 4 — Sonnet Synthesis

Send the final 12 items to Claude Sonnet (claude-sonnet-4-20250514).

System prompt: "You are an industry intelligence assistant for Evon Tay, a senior UX/DesignOps people manager in Singapore. She leads a 5-person team inside an AI-focused organisation (MosAIc AI Experience, under APEX Chief AI Office). The team is transitioning from conventional UX practice toward AI-ready workflows. Two priority skill gaps: (1) AI-ready workflows and fluency, (2) design leadership — strategy, facilitation, advocacy, systems thinking. Produce a weekly MosAIc Pulse digest. Be specific and practitioner-level. No generic observations. If source material is thin for a section, note it briefly — do not pad. The WEEK IN CONTEXT section should read as analytical prose — not a list. Write it as a short editorial: what is the field collectively saying this week, what tensions or patterns emerge across the signals, and what does it mean for a team navigating AI transition. Aim for 80-100 words, field-note register. Total digest under 600 words."

Output format the Sonnet prompt should request (EXACTLY — no deviations):

```
MOSAIC PULSE — Week of [DATE]

THIS WEEK'S THEME: [one sharp sentence connecting the week's signals]

WEEK IN CONTEXT
[80-100 words of analytical prose synthesising the week's signals into a coherent narrative.]

01 | CRAFT & PRACTICE
[2 items max — skip section if no relevant articles]
• [Source name] — [1-line summary] -> [URL]
  ↳ Why it matters: [1 sentence, specific to AI UX or DesignOps context]

02 | TOOLS & AI FLUENCY
[2 items max]
• [Source name] — [1-line summary] -> [URL]
  ↳ Try this: [one concrete low-lift action the team can take]

03 | DESIGN LEADERSHIP
[2 items max]
• [Source name] — [1-line summary] -> [URL]
  ↳ Leadership angle: [1 sentence on strategic implication]

04 | HORIZON WATCH
[1 item — the most forward-looking signal in the set]
• [Source name] — [1-line summary] -> [URL]
  ↳ 6-month implication: [one specific projection]

05 | SKILLS MARKET SIGNALS
[2 items max — skip section if no job postings in source material]
• [Job title] at [Company] -> [URL]
  ↳ What this demands: [2-3 specific skills or competencies]
  ↳ Team implication: [one sentence on skill development meaning]

SKILL OF THE WEEK
[Skill name] — [why relevant now, 1 sentence] — [one free resource -> URL]

REFLECTION PROMPT
[One thought-provoking question for team discussion — not generic]
```

## Step 5 — Email

Send the Sonnet output as a plain text email via SMTP SSL (port 465).

Subject line format: `[PULSE DRAFT] MosAIc Pulse — {today's date}`

Add a header line at the top of the email body:
`DRAFT — Review and edit before forwarding to your team.`
Followed by a divider line of dashes, then the Pulse content.

Add a footer at the bottom:
`Generated: {timestamp} | mosaic_pulse.py`

## Error handling

- If no items pass the date filter: send a short "nothing this week" email instead of the full digest
- If Haiku fails: fall back to first 10 items unscored
- If Sonnet fails: send an error notification email with the raw article list so the week is not lost
- Log all steps to console with timestamps

## Requirements file

Generate a `requirements.txt` with all needed packages.

## Running it

The script should run with: `python mosaic_pulse.py`

Print progress to console at each step so I can see what is happening:
- How many feeds were fetched successfully vs failed
- How many items passed the date filter
- How many items after domain capping
- How many items sent to Haiku
- Confirmation when email is sent

After building the script, show me how to schedule it with cron to run every Friday at 8am Singapore time (UTC+8, which is midnight UTC).
