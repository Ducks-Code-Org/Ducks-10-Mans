# Contributing

- For each issue, a new branch should be made off of `dev`
  - Naming format: `feature/<SHORT_DESCRIPTION>` or `bugfix/<SHORT_DESCRIPTION>`
- Once finished, a PR should be opened on the branch
- Once completed, the updated branch will be merged into `dev` by an authorized user

## Development Lifecycle and Versioning

- New development will go into the `dev` branch, and production will run on `main`
  - Direct commits onto `main` should be avoided, and only used for hotfixes, which will be versioned as (0.0.0 -> 0.0.1)
- `dev` will get merged into `main` weekly, with a new small version (0.0.0 -> 0.1.0)
- Larger releases of bigger features or massive functionality overhauls will be a new large version (0.0.0 -> 1.0.0)
