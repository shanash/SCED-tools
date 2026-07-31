# Fixture slice with a row removed

| Tier | Runner | Repo | When (KST) | Definition |
|------|--------|------|-----------|------------|
| primary | this Mac mini, launchd | `SCED-downloads` | 01:12 | `com.shanash.sced-daily-sync.SCED-downloads.plist` |
| primary | this Mac mini, launchd | `SCED` | 01:47 | `com.shanash.sced-daily-sync.SCED.plist` |
| fallback | GitHub Actions | `SCED-downloads` | 02:12 (`12 17 * * *` UTC) | `.github/workflows/daily-upstream-sync.yml` |

Trailing prose.
