# Cutover to the public repository

What moves when the Factory's source of truth switches from the private repository to its public
copy. Names only: no value of any secret appears here or in any report. Values are copied from
the private repository's settings screen into the public repository's settings screen.

## How the Factory deploys

GitHub Actions in the repository builds and rolls the services; there is no hosting webhook.

| Workflow | Runs on | What it does |
| --- | --- | --- |
| `deploy-aws.yml` | push to `master`, manual | builds the backend image to ECR, rolls ECS service `cerebrumdev-backend` in cluster `cerebrum` |
| `deploy-aws-frontend.yml` | push to `master`, manual | builds the frontend image (with `GIT_SHA`), rolls the frontend service |
| `post-deploy-smoke.yml` | after a deploy, push, manual | the release cycle against the live Factory |
| `ops.yml` | push, manual | operations tasks against the live Factory |
| `ci.yml`, `gate-replay.yml` | pull requests (gate replay also on push) | tests and the certified-build gate replay |
| `store-parity.yml` | schedule, manual | Store parity check |
| `dependabot-automerge.yml` | pull requests | dependency bumps |

The AWS side (cluster, services, task definitions, secrets in Secrets Manager) does not change:
the same workflows simply run from the public repository.

## Repository secrets to re-create

| Secret | Used by | Set on the private repository today |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | deploy-aws, deploy-aws-frontend, ops | yes |
| `AWS_SECRET_ACCESS_KEY` | deploy-aws, deploy-aws-frontend, ops | yes |
| `CROSS_REPO_PAT` | ci, deploy-aws, ops, store-parity (reads the Store repository) | yes |
| `SMOKE_GATE_TOKEN` | ops, post-deploy-smoke | yes |
| `SMOKE_EMAIL` | post-deploy-smoke | no (referenced, not set) |
| `SMOKE_PASSWORD` | post-deploy-smoke | no (referenced, not set) |

`GITHUB_TOKEN` is provided by Actions and needs nothing.

## Repository variables to create

| Variable | Used by | Set today |
| --- | --- | --- |
| `FACTORY_BASE_URL` | ops, post-deploy-smoke | no (0 variables on the private repository) |
| `SMOKE_FRONTEND` | post-deploy-smoke | no |

## Things that name the private repositories

`CROSS_REPO_PAT` reads the Store repository by name in `ci.yml`, `deploy-aws.yml`,
`store-parity.yml`; the backend's Store, builds and Factory repository names live in code and
`render.yaml`. When the Store and builds repositories move to their public copies, those names
change with them, or the token keeps reading the private ones until they are retired.

## Check after the first deploy from the public repository

- `https://api.cerebrum-dev.com/health` returns 200, `status: ok`.
- `https://api.cerebrum-dev.com/version` returns the public repository's commit.
- `https://cerebrum-dev.com/version` returns the same commit (needs PR #750).
- Build mode turned on, then `/health` shows `build_mode_enabled: true`.
- One sign-up as a new user receives the verification email (needs the sending domain verified).
