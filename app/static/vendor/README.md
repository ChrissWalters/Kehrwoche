# Vendored third-party files

## vue.esm-browser.prod.js

| | |
|---|---|
| Component | Vue 3 (`vue`), version **3.5.40** |
| Licence | MIT (banner kept in the file, see its first lines) |
| Origin | npm registry, `https://registry.npmjs.org/vue/-/vue-3.5.40.tgz`, path `package/dist/vue.esm-browser.prod.js` |
| Retrieved | 2026-08-03 |
| SHA-256 | `2e1777387ce6985aa839f465cfc688e31fe283124146b007a253eb0cb8f4a6a5` |
| Size | 170 432 bytes |

The file is **byte-identical to the published release**; nothing was added or removed, so
the checksum above can be re-verified against upstream at any time.

### How it was verified

1. Package metadata fetched from `registry.npmjs.org` over TLS.
2. npm's registry signature on `vue@3.5.40:<integrity>` checked against npm's published
   signing key `SHA256:DhQ8wR5APBvFHLF/+Tc+AYvPOdTpcIDqOhxsBHRwC7U`
   (`ecdsa-sha2-nistp256`, no expiry) from `registry.npmjs.org/-/npm/v1/keys` — **valid**.
3. SHA-512 of the downloaded tarball compared against the `dist.integrity` value from the
   signed metadata — **match**.
4. The extracted file compared byte for byte against the copy served by an independent
   mirror (`cdn.jsdelivr.net`) — **identical**.

`tests/test_frontend.py` re-checks the SHA-256 on every test run, so an accidental or
unnoticed change to the file breaks the build.

To update: repeat steps 1–4 for the new version, replace the file and update this page,
the checksum in the test and the entry in `README.md`.

### Why the full build

The specification names this file. It contains the template compiler, which compiles
through `Function("Vue", code)` — and that needs `'unsafe-eval'` in the Content Security
Policy. Kehrwoche therefore writes **all components as render functions** (`h(...)`), so
that code path is never reached and the strict CSP from AP29 (`default-src 'self'`) holds.
