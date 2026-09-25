from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ROOT / "desktop-react" / "src" / "components" / "ToolIdentity.tsx"
LIBRARY = ROOT / "desktop-react" / "src" / "components" / "ToolIconLibrary.tsx"
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"


def test_task_flow_has_brand_and_capability_identity_families() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    for family in (
        '"github"',
        '"openai"',
        '"anthropic"',
        '"gemini"',
        '"deepseek"',
        '"minimax"',
        '"xai"',
        '"sentinelx"',
        '"browser"',
        '"computer"',
        '"workspace"',
        '"search"',
        '"memory"',
        '"skills"',
        '"calculator"',
        '"approval"',
        '"subagent"',
        '"automation"',
        '"mcp"',
    ):
        assert family in source

    assert 'kind: "brand"' in source
    assert 'kind: "capability"' in source
    assert 'kind: "neutral"' in source


def test_vector_library_contains_real_brand_marks_and_custom_capabilities() -> None:
    source = LIBRARY.read_text(encoding="utf-8")

    for brand in (
        "GitHubGlyph",
        "OpenAIGlyph",
        "AnthropicGlyph",
        "GeminiGlyph",
        "DeepSeekGlyph",
        "MiniMaxGlyph",
        "XAIGlyph",
    ):
        assert f"function {brand}" in source

    for capability in (
        "SentinelXGlyph",
        "TerminalGlyph",
        "FileEditGlyph",
        "BrowserGlyph",
        "ComputerGlyph",
        "WorkspaceGlyph",
        "SearchGlyph",
        "MemoryGlyph",
        "SkillGlyph",
        "CalculatorGlyph",
        "ApprovalGlyph",
        "SubAgentGlyph",
        "AutomationGlyph",
        "MCPGlyph",
        "GenericToolGlyph",
    ):
        assert f"function {capability}" in source

    assert "The shapes are the real vendor marks" in source
    assert "These are intentionally custom rather than vendor-branded" in source


def test_identity_resolver_prefers_known_providers_and_keeps_safe_fallback() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    for provider in ("github", "openai", "anthropic", "claude", "gemini", "deepseek", "minimax", "grok"):
        assert provider in source.lower()

    assert 'name === "computer_action"' in source
    assert 'name.startsWith("browser_")' in source
    assert 'name === "web_search"' in source
    assert 'name === "spawn_agent"' in source
    assert "return IDENTITIES.generic;" in source


def test_group_icons_do_not_fall_back_to_legacy_lucide_special_cases() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    block = source[source.index("export function activityGroupIdentity"):source.index("function IdentityGlyph")]
    assert 'GROUP_IDENTITIES["terminal-group"]' in block
    assert 'GROUP_IDENTITIES["file-group"]' in block
    assert 'GROUP_IDENTITIES["mixed-group"]' in block
    assert "return first;" in block


def test_identity_glyphs_are_self_describing_and_transcript_uses_them() -> None:
    identity = IDENTITY.read_text(encoding="utf-8")
    transcript = TRANSCRIPT.read_text(encoding="utf-8")

    assert 'className={`tool-identity-glyph is-${identity.kind}`}' in identity
    assert "data-tool-kind={identity.kind}" in identity
    assert "data-tool-family={identity.family}" in identity
    assert "activityToolLabel(item)" in transcript
    assert "ActivityGroupGlyph" in transcript


def test_brand_mcp_labels_drop_internal_canonical_prefix() -> None:
    source = IDENTITY.read_text(encoding="utf-8")

    assert "canonicalMcp" in source
    assert "humanizeRemoteTool" in source
    assert "identity.kind === \"brand\"" in source
