# Fixture slice of CLAUDE.md

Prose before the table is never rewritten.

| Tier | Runner | Repo | When (KST) | Definition |
|------|--------|------|-----------|------------|
| primary | this Mac mini, launchd | `SCED-downloads` | 01:12 | `com.shanash.sced-daily-sync.SCED-downloads.plist` |
| primary | this Mac mini, launchd | `SCED` | 01:47 | `com.shanash.sced-daily-sync.SCED.plist` |
| fallback | GitHub Actions | `SCED-downloads` | 02:12 (`12 17 * * *` UTC) | `.github/workflows/daily-upstream-sync.yml` |
| fallback | GitHub Actions | `SCED` | 02:47 (`47 17 * * *` UTC) | `.github/workflows/daily-upstream-sync.yml` |

The 30-minute gap between the two local agents keeps them off each other's
workspace-wide lock; the 60-minute gap to the fallback is the window the primary
has to finish in.
