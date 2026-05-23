from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_multi_view_page_filters_models_by_capability_instead_of_fixed_ids() -> None:
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "MultiViewPage.tsx").read_text(encoding="utf-8")

    assert "supports_reference_images && model.supports_multi_image_fusion" in source
    assert "allowedMultiViewModelIds" not in source


def test_module_pages_load_history_by_kind_instead_of_filtering_global_page() -> None:
    source = (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "const MODULE_HISTORY_PAGE_SIZE = 100" in source
    assert "fetchPersistedHistory(user.role === \"root\", 1, MODULE_HISTORY_PAGE_SIZE, kind, null)" in source
    assert "onRefreshHistory={() => refreshModuleHistory(\"multi_view\")}" in source
