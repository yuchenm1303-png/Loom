from concurrent.futures import ThreadPoolExecutor

from services.loom_model_policy.image_catalog import ImageCatalog
from services.loom_model_policy.catalog_sync import CatalogSynchronizer, parse_models, parse_opencode_console_config
from services.loom_model_policy.policy import PolicyStore


MODEL = "minimax-m3.1-flash-free"
BASE = "https://opencode.ai/zen/go/v1"


def published(modalities=None, api="https://opencode.ai/inference/go/openai/v1"):
    return {"opencode-go": {"models": {MODEL: {"id": MODEL, "modalities": {"input": modalities or ["text", "image"]}, "provider": {"api": api}}}}}


def rows():
    return parse_models("opencode-go", {"data": [{"id": MODEL}]})


def test_sparse_listing_receives_exact_provider_modalities_without_adding_models():
    catalog = ImageCatalog(fetch=published)
    before = rows()
    after = catalog.enrich("opencode-go", BASE, before)
    assert after[0]["image_input"] == {"supported": True, "source": "models.dev/opencode-go:modalities"}
    assert before[0]["image_input"]["supported"] is None
    assert len(after) == 1


def test_text_only_declaration_is_not_guessed_from_name():
    catalog = ImageCatalog(fetch=lambda: published(["text"]))
    assert catalog.enrich("opencode-go", BASE, rows())[0]["image_input"]["supported"] is False
    other = parse_models("opencode-go", {"data": [{"id": "vision-vl-image"}]})
    assert catalog.enrich("opencode-go", BASE, other)[0]["image_input"]["supported"] is None


def test_endpoints_and_providers_are_not_interchangeable():
    for base in ["https://custom.example/v1", "https://opencode.ai.evil.test/zen/go/v1", "https://opencode.ai/zen/go/v1?other=1"]:
        assert ImageCatalog(fetch=published).enrich("opencode-go", base, rows()) == rows()
    catalog = ImageCatalog(fetch=lambda: published(api="https://opencode.ai/inference/openai/v1"))
    assert catalog.enrich("opencode-go", BASE, rows())[0]["image_input"]["supported"] is None
    console = parse_models("opencode-console", {"data": [{"id": MODEL}]})
    assert ImageCatalog(fetch=published).enrich("opencode-console", "https://opencode.ai/inference/openai/v1", console) == console


def test_explicit_upstream_and_conflicting_metadata_are_never_overwritten():
    catalog = ImageCatalog(fetch=published)
    for item in [{"id": MODEL, "vision": False}, {"id": MODEL, "vision": False, "input_modalities": ["image"]}]:
        original = parse_models("opencode-go", {"data": [item]})
        assert catalog.enrich("opencode-go", BASE, original) == original


def test_public_metadata_failures_do_not_block_discovery_and_cache_expires(tmp_path):
    clock = [0]
    failed = [False]
    calls = []
    def fetch():
        calls.append(True)
        if failed[0]:
            raise OSError("offline")
        return published()
    catalog = ImageCatalog(fetch=fetch, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: catalog.enrich("opencode-go", BASE, rows()), range(4)))
    assert len(calls) == 1
    assert all(row[0]["image_input"]["supported"] for row in results)
    failed[0] = True
    clock[0] = 3601
    assert catalog.enrich("opencode-go", BASE, rows())[0]["image_input"]["supported"] is True
    clock[0] = 86401
    assert catalog.enrich("opencode-go", BASE, rows())[0]["image_input"]["supported"] is None
    store = PolicyStore(tmp_path / "policy.db")
    sync = CatalogSynchronizer(store, environ={"OPENCODE_GO_API_KEY": "fixture"},
        fetch=lambda *_: {"data": [{"id": MODEL}]}, image_catalog=catalog)
    assert sync.refresh()
    row = next(row for row in store.catalog()["models"] if row["model"] == MODEL and row["group_id"] == "opencode-go")
    assert row["available"] and row["image_input"]["supported"] is None


def test_complete_sync_persists_evidence_with_model_identity(tmp_path):
    store = PolicyStore(tmp_path / "policy.db")
    sync = CatalogSynchronizer(store, environ={"OPENCODE_GO_API_KEY": "fixture"},
        fetch=lambda *_: {"data": [{"id": MODEL}]}, image_catalog=ImageCatalog(fetch=published))
    sync.refresh()
    access = store.effective_access(7, {"enabled": True})
    row = next(row for row in access["catalog"]["models"] if row["model"] == MODEL and row["group_id"] == "opencode-go")
    assert row["image_input"]["supported"] is True
    assert next(rule for rule in access["decisions"] if rule["model_id"] == row["model_id"])["enabled"]


def test_console_configuration_preserves_explicit_input_modalities():
    rows = parse_opencode_console_config({"providers": {"opencode": {"package": "@ai-sdk/openai-compatible",
        "settings": {"baseURL": "https://opencode.ai/inference/openai/v1"},
        "models": {MODEL: {"name": MODEL, "modalities": {"input": ["text", "image"]}}}}}})
    assert next(row for row in rows if row["model"] == MODEL)["image_input"]["supported"] is True


def test_malformed_other_provider_metadata_does_not_break_discovery():
    data = published()
    data["opencode"] = {"models": []}
    catalog = ImageCatalog(fetch=lambda: data)
    original = parse_models("opencode-console", {"data": [{"id": MODEL}]})
    assert catalog.enrich("opencode-console", "https://opencode.ai/zen/v1", original) == original
