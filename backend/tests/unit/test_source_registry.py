from app.integrations.sources.registry import build_default_registry


def test_default_registry_has_dou_and_djinni() -> None:
    registry = build_default_registry()

    names = {adapter.source_name for adapter in registry.all()}
    assert names == {"dou", "djinni"}
    assert registry.get("dou").source_name == "dou"
    assert registry.get("djinni").source_name == "djinni"
