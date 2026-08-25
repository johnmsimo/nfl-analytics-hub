# Main branch protection contract

`main` is the production branch. Configure its GitHub branch protection rule
with the checked-in [`main-protection.json`](main-protection.json) contract.

The rule must:

- require a pull request before merging while allowing the solo maintainer to
  merge without a second approving account;
- require branches to be up to date before merging;
- require the `test`, `quality`, and `analytics-tests` status checks;
- require all review conversations to be resolved;
- apply to repository administrators;
- block force pushes and branch deletion.

The required check names are intentionally the stable job identifiers from
`.github/workflows/ci.yml` and `.github/workflows/quality.yml`. Renaming those
jobs requires updating the protection rule and this contract in the same pull
request.

The Fly deployment workflow is a second independent gate. It has no manual
deployment bypass, accepts only a successful push-triggered `CI` run on
`main`, and checks out that run's exact commit. Immediately before invoking
Fly it confirms that the commit is still the head of `main`. A newer successful
CI run cancels any older in-progress production workflow, so production moves
forward through tested commits rather than waiting behind a superseded deploy.

An administrator can apply the checked-in contract with GitHub's branch
protection API:

```bash
gh api \
  --method PUT \
  --header 'Accept: application/vnd.github+json' \
  repos/johnmsimo/nfl-analytics-hub/branches/main/protection \
  --input .github/main-protection.json
```

After applying it, verify that direct pushes and force pushes are blocked and
that a pull request cannot merge until all three required checks pass.
