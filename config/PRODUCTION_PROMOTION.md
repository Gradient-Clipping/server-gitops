# Validated production branch

`main` receives reviewed configuration and the single Flux image writer's image
updates. `Validate desired state` validates the complete candidate; only its
dependent promotion job may advance `production`. The promotion script verifies
the exact source SHA, workflow, event, repository, attempt and successful
validation job. It ignores stale candidates and permits fast-forward updates
only. Reverting production means a new tested revert commit on `main`, never a
forced rewind of `production`.

Promotion uses the workflow's short-lived `GITHUB_TOKEN`, without a personal
token or a new production-server credential. Repository push webhooks are
distinct from GitHub Actions workflow triggering. A real production promotion
must be verified through the existing push receiver and Flux revision.

The domain reconciler image is built and tested in its dedicated publishing
workflow. Desired-state validation retains rendering and unit checks but does
not rebuild an unchanged controller image for every automated image commit.

Rollout is staged: establish the validated production branch first, then switch
the Git Source and push receiver to production through a controlled, versioned
cutover. ImageUpdateAutomation must continue checking out and pushing `main` so
image changes cannot bypass validation.

This workflow is a deployment gate. Private-repository branch restrictions may
require a GitHub plan supporting rulesets; it does not claim to revoke an owner's
ability to edit workflows or directly write refs. Keep write access restricted,
and use repository branch rules when available.
