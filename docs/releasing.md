# Releasing

The git tag is the only place a version number lives. Docs and stack files refer to
`latest` or `<version>`, so nothing else needs bumping.

1. In [`CHANGELOG.md`](../CHANGELOG.md), rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]` above it, and update the compare links at the bottom:

   ```markdown
   [Unreleased]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/vX.Y.Z...HEAD
   [X.Y.Z]: https://github.com/haythamelkhoja/pixoo-radar-enhanced/compare/vPREVIOUS...vX.Y.Z
   ```

2. Commit, tag and push:

   ```sh
   git commit -am "Release X.Y.Z"
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

The [workflow](../.github/workflows/docker.yml) then:

- builds the image for amd64 and arm64 and pushes `X.Y.Z`, `X.Y`, `X` and `latest` to ghcr.io;
- creates the GitHub Release `vX.Y.Z` with the `## [X.Y.Z]` changelog section as its notes.
  It fails if that section is missing; add it, then re-run the job.

`install.sh` installs whatever GitHub marks as the latest release, so systemd hosts pick
up the new version the next time it's run.

Always release by pushing a tag. A release created in the GitHub web UI doesn't build an image.
