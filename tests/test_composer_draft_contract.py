from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSER = ROOT / "desktop-react" / "src" / "components" / "Composer.tsx"
COMPOSER_BASE = ROOT / "desktop-react" / "src" / "components" / "ComposerBase.tsx"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_composer_draft_is_owned_above_idle_and_running_variants() -> None:
    composer = read(COMPOSER)

    assert 'const [drafts, setDrafts] = useState<Record<string, string>>({});' in composer
    assert 'const draftKey = props.threadId || UNBOUND_DRAFT_KEY;' in composer
    assert 'const draftValue = drafts[draftKey] ?? "";' in composer
    assert 'onDraftValueChange: setDraftValue' in composer
    assert 'props.running' in composer
    assert '<SteeringComposer {...sharedProps} />' in composer
    assert '<ComposerBase {...sharedProps} />' in composer


def test_both_composer_surfaces_consume_the_shared_draft() -> None:
    composer = read(COMPOSER)
    base = read(COMPOSER_BASE)

    assert "draftValue," in composer
    assert "onDraftValueChange," in composer
    assert 'const value = draftValue ?? localValue;' in composer
    assert 'const setValue = onDraftValueChange ?? setLocalValue;' in composer

    assert "draftValue?: string;" in base
    assert "onDraftValueChange?(value: string): void;" in base
    assert 'const value = draftValue ?? localValue;' in base
    assert 'const setValue = onDraftValueChange ?? setLocalValue;' in base


def test_drafts_are_isolated_by_thread_and_cleared_only_for_that_thread() -> None:
    composer = read(COMPOSER)

    assert 'return { ...current, [draftKey]: nextValue };' in composer
    assert 'delete next[draftKey];' in composer
