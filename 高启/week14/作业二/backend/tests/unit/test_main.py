"""覆盖 FastAPI 工厂装配所有 HTTP 路由并注入应用运行依赖。"""

from types import SimpleNamespace
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.main import FRONTEND_DIR, create_app, create_frontend_preview_app


class EmptyDocumentStore:
    """提供应用路由装配时需要的空文档读取接口。"""

    def get(self, document_id):
        """测试应用没有已上传文档。"""
        return None


class EmptyChatManager:
    """提供问答路由所需的占位管理器。"""

    pass


def test_create_app_includes_document_and_chat_routes_without_external_services(tmp_path) -> None:
    """注入运行依赖时可构造 FastAPI，路由覆盖文档和问答接口。"""
    settings = Settings.from_env({"RAG_DATA_ROOT": str(tmp_path)})
    runtime = SimpleNamespace(
        settings=settings,
        document_store=EmptyDocumentStore(),
        upload_dependencies=SimpleNamespace(),
        chat_manager=EmptyChatManager(),
        consumer_manager=SimpleNamespace(),
        start=lambda: None,
        stop=lambda: None,
    )

    app = create_app(settings, runtime=runtime)

    paths = {
        route.path
        for route_group in app.routes
        for route in getattr(route_group, "original_router", SimpleNamespace(routes=[])).routes
    }
    paths.update(route.path for route in app.routes if hasattr(route, "path"))
    assert "/api/documents" in paths
    assert "/api/documents/{document_id}" in paths
    assert "/api/chat" in paths
    assert "/api/chat/{chat_id}" in paths
    assert "/api/chat/{chat_id}/confirmation" in paths
    assert "/api/assets/{document_id}/{asset_id}" in paths
    assert app.state.runtime is runtime
    assert "/api/chat" in app.openapi()["paths"]
    index_route = next(route for route in app.routes if getattr(route, "path", None) == "/")
    static_mount = next(route for route in app.routes if getattr(route, "path", None) == "/static")
    page = index_route.endpoint()
    assert Path(page.path) == FRONTEND_DIR / "index.html"
    assert "多模态 RAG 问答" in Path(page.path).read_text(encoding="utf-8")
    assert Path(static_mount.app.directory, "src", "main.mjs").is_file()


def test_frontend_preview_has_only_local_page_and_static_assets() -> None:
    """静态预览不装配业务 API 或需要外部服务的运行依赖。"""
    app = create_frontend_preview_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    index_route = next(route for route in app.routes if getattr(route, "path", None) == "/")
    static_mount = next(route for route in app.routes if getattr(route, "path", None) == "/static")

    assert "/api/chat" not in paths
    assert "/api/documents" not in paths
    assert Path(index_route.endpoint().path) == FRONTEND_DIR / "index.html"
    assert Path(static_mount.app.directory, "styles", "app.css").is_file()
