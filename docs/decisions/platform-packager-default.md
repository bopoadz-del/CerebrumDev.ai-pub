# Decision Brief: Default Deploy Target

## Context

The deploy router currently defaults to `target="cloud"`, which routes through `package_session` (the original cloud/edge packager). Two newer options exist:

- `package_platform_session` — a production-hardened "Fork-class" platform package.
- Edge packaging — a self-contained zip built from the `package_session` output.

This brief compares three options for the default target.

## Options

### (a) Keep `package_session` (cloud/edge) as the default

- **Image size**: Smaller because it clones only `Cerebrum-Blocks` at build time and layers session files on top.
- **Build time**: Faster; single-stage Dockerfile, no frontend build, no Postgres/alembic bootstrap.
- **Operational complexity**: Lower for a simple cloud Render deploy; higher for operators who expect a production-ready stack.
- **Self-containment limitation**: The package is *not* fully self-contained. The generated Dockerfile runs `git clone` against the public `Cerebrum-Blocks` repo, so the runtime source is fetched at build time rather than vendored. If the upstream repo moves, changes, or becomes unreachable, historical packages may not rebuild reproducibly.
- **Smoke-test finding**: The edge package built this way is **not runnable standalone**; it still expects the Cerebrum-Blocks runtime to be present.

### (b) Make `package_platform_session` the default

- **Image size**: Larger because it is multi-stage, copies `site-packages` and `/usr/local/bin` from the builder, and may include a built frontend.
- **Build time**: Longer; multi-stage Docker build, optional `npm ci` + `npm run build`, Postgres/pgvector via docker-compose, alembic migrations on boot.
- **Operational complexity**: Higher locally (needs Postgres), but lower for production because secrets, migrations, healthchecks, and a non-root user are built in.
- **Self-containment**: Better. The package contains the session artifacts, Dockerfile, docker-compose, Render blueprint, entrypoint, and a vendored copy of the resolved `Cerebrum-Blocks` engine under `engine/`. The generated Dockerfile uses `COPY engine/ /app` and does not run `git clone` during the build.
- **Packaging-time engine dependency**: Resolved by fetching the engine at packaging time when no local checkout is present. The fetch is shallow, cached by ref, and records `{repo, ref, commit_sha, vendored: true, vendored_path: "engine/"}` in `build_metadata.json`.
- **How (b) resolves the edge limitation**: Platform packages are explicitly production-oriented; users receive a docker-compose stack rather than a "drop this into an engine checkout" zip, so the "not runnable standalone" confusion goes away.

CerebrumDev.ai vendors the resolved Cerebrum-Blocks engine snapshot into generated platform packages. This makes exports reproducible and avoids deployment-time dependency on floating GitHub clones.

### (c) Refactor the edge package to embed a minimal engine runtime

- **Image size**: Largest option. Embedding a full or stripped `Cerebrum-Blocks` runtime (plus Python dependencies) inside every zip would make each package hundreds of megabytes.
- **Build time**: Slowest; the backend would need to clone, install dependencies, and snapshot the runtime for every session package.
- **Operational complexity**: Highest for the packager/backend; lowest for the operator, because the zip would truly be standalone.
- **Trade-off**: True offline/standalone capability at the cost of package size, build time, and backend complexity.
- **Smoke-test finding**: This is the only option that would make the edge package runnable without any external clone step.

## Decision

**DECIDED — option (b): make `package_platform_session` the default deploy target.**

Date: 2026-07-06

Rationale:

1. The orchestrator-bearing platform package is the product CerebrumDev.ai ships; edge is a lightweight preview.
2. The smoke test showed that the current cloud/edge package confuses users because it is not actually standalone.
3. The platform package already includes the production hardening (Postgres, migrations, secrets, healthchecks, unprivileged user) that cloud deploys will eventually need.
4. The operational complexity of (c) — embedding a runtime per package — is disproportionate to the current user base and would bloat every package.
5. The blocker for (b) is solved by packaging-time engine discovery with a pinned ref and shallow cache.
