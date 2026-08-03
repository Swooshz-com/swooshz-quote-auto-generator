# Run-34 retrospective fixture v1.0.0

This directory is a repository-contained, closed execution fixture for
retrospective evidence. It is not the original RED chronology and it does not
claim to recreate the original development sequence.

The preserved test change is the exact repository evidence patch. The
executable test module is the narrow historical projection needed by the
declared thirteen-test selection. The validator and documentation files are
the only historical source fragments required by that selection. Their
provenance is recorded in `manifest.json`; no Git object, branch, tag, or
network retrieval is needed at execution time.

The dependency snapshot is intentionally standard-library-only on the exact
CPython 3.12.13 runtime. An empty third-party package set is explicit and
closed; adding a package, changing the runtime declaration, or changing any
manifest-bound input invalidates the fixture.

The expected result is exactly thirteen assertion failures, zero errors, zero
unexpected passes, and zero skipped required tests. The bounded receipt is
public-safe and reports no child test output.
