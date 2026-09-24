# contracts/openapi/v0.2/

Skeleton of the v0.2.0 resource-centric HTTP contract (ADR-0018, parent
design issue #34). `../v0.1.yaml` is untouched and stays the contract for the
v0.1 learner chain while v0.2 is built up resource by resource.

## Layout

```
root.yaml                  info/servers(/v2)/components; paths is always {}
paths/<resource>.yaml       URL -> Path Item map for one resource
components/common.yaml      Problem (RFC 9457) and the placeholder Stub schema
components/<resource>.yaml  resource-specific schemas (added by that resource's PR; none yet)
```

Resources (ADR-0018): `session`, `ws` (workspace, not to be confused with the
`websocket` protocol contracts under `contracts/schemas/websocket/`), `memo`,
`text`, `drill`, `artifact`, `chat`, `highlight`, `events`, `notebook`.

## Merge layout — `root.yaml` never needs editing

`root.yaml`'s `paths:` is `{}` on disk, always. Each `paths/<resource>.yaml`
is a plain URL -> Path Item map, e.g.:

```yaml
# paths/session.yaml
/sessions:
  post: ...
/sessions/{session_id}:
  get: ...
```

`harness.testing.openapi_v2.load_merged_openapi_v2_spec()` loads `root.yaml`
and splices every `paths/*.yaml` into its `paths` at load time, raising
`DuplicatePathError` if two files declare the same URL. This is what
`tests/contracts/test_openapi_v2_contract.py` validates; a future harness API
or docs surface that serves the v0.2 document can reuse the same loader.

**A resource PR edits only its own `paths/<resource>.yaml`** (and may add a
new `components/<resource>.yaml` for schemas only that resource needs). It
never touches `root.yaml`, `components/common.yaml`, or another resource's
files — a brand new URL is just a new top-level key in your own file, so two
resource PRs adding different URLs in parallel never conflict on the same
lines of a shared file. If two resource PRs genuinely claim the *same* URL,
`load_merged_openapi_v2_spec` raises `DuplicatePathError` and the contract
test fails, which is the intended way to catch that.

`/<resource>/_stub` in each file today is a placeholder `GET` operation with
no meaning, present only so this skeleton is a valid, fully-resolving OpenAPI
document before any real endpoint exists. A resource PR replaces or extends
it with real paths in the same file.

A `$ref` inside a `paths/<resource>.yaml` (e.g. `../components/common.yaml#/Stub`,
`../root.yaml#/components/responses/Problem`) is written relative to that
file's own directory, same convention as `root.yaml`'s own refs into
`components/common.yaml`. The loader rewrites these to absolute `file://`
URIs at merge time so they still resolve once spliced into the merged
document (see the module docstring).

## Validator

Same validator as v0.1: `openapi-spec-validator`'s `OpenAPIV31SpecValidator`,
via `jsonschema_path.SchemaPath.from_dict` on the merged spec, with `base_uri`
set to `root.yaml`'s own file URI so `$ref`s into `components/common.yaml`
resolve (verified against the installed `jsonschema_path` source —
`SchemaPath.from_file_path`/`PathReader` compute `base_uri` from
`Path.as_uri()`, and the default `file` handler resolves relative refs
against it; the same mechanism `contracts/openapi/v0.1.yaml`'s test uses for
absolute `https://morphloop.dev/contracts/...` refs, just with `file` instead
of `https`/`http`). See `tests/contracts/test_openapi_v2_contract.py`.
