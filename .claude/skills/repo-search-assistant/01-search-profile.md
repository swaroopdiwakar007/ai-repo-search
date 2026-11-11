# Search Profile

## What I am looking for
Open-source AI agents that run a job search for an individual: they find job postings, score them against the user's CV or profile, and help with applications (tailored CV, cover letter, interview prep). They should run locally, ideally inside Claude Code or a similar AI coding CLI.

## Why
Compare alternatives to ai-job-search-dev/ai-job-search, borrow design ideas, and decide whether a different project fits better.

## Seed repositories
- ai-job-search-dev/ai-job-search

## Must-have capabilities
- Fit scoring or ranking of job postings against a user profile or CV
- Tailored application documents (CV and/or cover letter) per posting
- Runs locally from the user's machine (CLI or agent harness), not a hosted SaaS

## Nice-to-have capabilities
- Job-board scraping or search across multiple portals
- Interview preparation
- Application tracker or dashboard
- Drafter / reviewer split or other self-review of generated documents
- Defences against prompt injection in scraped postings

## Technical preferences
- **Languages:** Python, TypeScript, JavaScript
- **Runtime / platform:** Claude Code or another AI coding CLI; plain CLI also fine
- **Acceptable licenses:** MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, MPL-2.0, GPL-3.0

## Deal-breakers (veto)
- Archived repository
- No license
- Last push older than 2 years
- Auto-submits applications with no human review step

## Soft preferences (lower the score, never veto)
- Fewer than 50 stars
- No tests or CI
- README without usage examples
