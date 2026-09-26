# Working on the UDP recorder

Follow [README.md](README.md) to clone, install dependencies, and create your own
`.env.local`. Credentials, recordings, generated files and unrelated local
workspace tools are excluded from this repository.

## Credentials and workspace

`MARPLE_API_TOKEN` is used for Trino verification. The SDK uses
`MARPLE_DB_API_TOKEN` when supplied, otherwise it reuses `MARPLE_API_TOKEN`.
The SDK token needs permission to create, append and cool live datasets, plus
signal updates for restoration. Never commit tokens.

Set the query host, user, hot/cold catalogs and datapool in `.env.local` for your
Marple workspace. `xplane_marple.py` reads those settings for verification.
Defaults match the aerospace demo. Run only one recorder against the shared demo
stream at a time: sequence reservations are local and do not coordinate simultaneous
writers on different computers.

The SDK is pinned to the flight-tested `marpledata==3.4.0.dev1`, available on PyPI.
Upgrade deliberately and validate the realtime lifecycle before an event.

## Validation

With the virtual environment activated:

```sh
python -m unittest discover -s tests -p 'test_xplane*.py'
```

Tests use mocks, temporary files and local sockets. They require no running
simulator or cloud credentials. A real flight is still needed to validate UDP
delivery, simulator dialogs, pause behavior and Marple performance.

For a local-only simulator check:

```sh
python start_xplane_service.py --local-only
```

## Share changes

After the repository owner gives you write access:

```sh
git switch -c my-change
# edit and validate
git add PATHS_TO_CHANGED_FILES
git commit -m "Describe the change"
git push -u origin my-change
```

Open a pull request for review. Each colleague keeps their own credentials and
`outputs/`. The root `.gitignore` allowlist keeps this repository scoped to the
recorder; update it explicitly if adding a new recorder module outside `xplane_*.py`.
