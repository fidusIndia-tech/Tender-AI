# Deployment Workflow

Use this workflow to keep `main` production-safe and avoid Railway serving older or half-tested commits.

## Branching Rule

- `main` = production only
- feature work = separate branch, for example:
  - `results-fix`
  - `gem-watcher-dev`
  - `evaluation-dev`

Do not push incomplete experiments directly to `main`.

## Local Development Flow

1. Create a branch from `main`.
2. Make and test changes locally.
3. Verify the exact behavior in local app first.
4. Commit only the files related to that feature/fix.
5. Push the feature branch.
6. Merge to `main` only when ready for production.

Example:

```powershell
git switch main
git pull origin main
git switch -c results-fix
```

After testing:

```powershell
git add tender_app/static/index.html
git commit -m "Fix GeM results handoff"
git push origin results-fix
```

Then merge to `main` only when approved.

## Railway Production Rule

Railway should deploy:

- repository: `fidusIndia-tech/Tender-AI`
- branch: `main`
- auto deploy: enabled only for `main`

If Railway is set correctly, production should only ever receive merged `main` commits.

## Before Testing Production

Always verify these three things in order:

1. GitHub `main` contains the fix.
2. Railway active deployment commit matches the latest GitHub `main` commit.
3. Then test production in incognito or after `Ctrl+F5`.

If Railway active commit does not match GitHub `main`, do not trust the production behavior yet.

## Safe Production Checklist

Before merge to `main`:

- local behavior verified
- only intended files staged
- no logs, extracted JSONs, or temporary files included
- commit message clearly describes the change

Before validating production:

- Railway deploy finished successfully
- active Railway commit matches GitHub `main`
- browser cache cleared or incognito used

## Recommended Habit

For production fixes:

- push one small fix at a time
- wait for Railway to finish that exact commit
- validate production
- only then push the next fix

This avoids confusion where GitHub is correct but Railway is still serving an older commit.
