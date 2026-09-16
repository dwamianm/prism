# WiCE claim-verification assay

This integration evaluates PRME's optional local `ClaimVerifier` against the
human-annotated WiCE claim test set. It uses WiCE's **oracle retrieval** chunks,
so it measures verification after perfect evidence retrieval. It does not
measure PRME retrieval quality or the original full-document WiCE task.

Commit and push the registration before inference. The runner accepts its bound
PRME revision only when that revision is an ancestor of the execution checkout
and the current verifier and runner bytes still match. The registration binds
the PRME revision, verifier and runner hashes, WiCE
revision, exact test-file hash, model revision, thresholds, policies, dataset
shape, and quality gates before inference. The result contains IDs, labels,
decisions, scores, and digests but does not copy claim or evidence text.
The registered preprocessing omits empty sentence placeholders found in nine
oracle chunks; all remaining text and sentence order are preserved.

Obtain a clean checkout of the registered WiCE revision and install the optional
verification runtime:

```shell
git clone https://github.com/ryokamoi/wice.git /path/to/wice
git -C /path/to/wice checkout REGISTRATION_REVISION
pip install 'prme[verification]'
```

Run a preregistered assay:

```shell
python -m benchmarks.integrations.run_wice_claim_verification \
  --registration /path/to/registration.json \
  --project-root . \
  --dataset-root /path/to/wice \
  --output /path/to/result.json
```

WiCE's annotations are ODC-BY. Its source documents remain subject to the
Wikipedia CC-BY-SA license and Common Crawl terms described in the upstream
`LICENSE.md`. Keep the external checkout outside PRME; do not commit the corpus.

Only WiCE's `supported` label counts as support. `partially_supported` and
`not_supported` are negative classes. This prevents a claim with one unsupported
component from passing as safe. The product metric accepts only PRME's guarded
`supported` status. The result also reports a raw entailment-threshold baseline
from the same model scores to isolate the effect of PRME's deterministic guards.
