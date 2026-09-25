from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ROOT / "desktop-react" / "src" / "components" / "ToolIdentity.tsx"
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"


def test_task_flow_has_distinct_provider_and_tool_family_icons() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    for family in (
        '"github"',
        '"sentinelx"',
        '"browser"',
        '"computer"',
        '"workspace"',
        '"search"',
        '"memory"',
        '"skills"',
        '"calculator"',
        '"mcp"',
    ):
        assert family in source

    for icon in (
        "Github",
        "ShieldCheck",
        "Globe2",
        "Monitor",
        "FolderOpen",
        "Search",
        "Database",
        "Sparkles",
        "Calculator",
        "Server",
    ):
        assert f"icon: {icon}" in source


def test_identity_resolver_prefers_known_providers_and_keeps_safe_fallback() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    assert "github" in source.lower()
    assert "sentinel" in source.lower()
    assert 'name === "computer_action"' in source
    assert 'name.startsWith("browser_")' in source
    assert 'name === "web_search"' in source
    assert 'return IDENTITIES.generic;' in source


def test_homogeneous_groups_inherit_identity_but_mixed_groups_fall_back() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    block = source[source.index("export function activityGroupIdentity"):source.index("export function ActivityGlyph")]
    assert "identities.every" in block
    assert "? first" in block
    assert ": IDENTITIES.generic" in block


def test_transcript_renders_resolved_identity_for_rows_and_groups() -> None:
    source = TRANSCRIPT.read_text(encoding="utf-8")

    assert "data-tool-family={identity.family}" in source
    assert "data-tool-family={groupIdentity.family}" in source
    assert "activityToolLabel(item)" in source
    assert "ActivityGroupGlyph" in source
    assert 'if (identity.family === "generic") return <Wrench' in source
