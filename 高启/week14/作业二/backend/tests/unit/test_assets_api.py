"""验证图片资源路由的归属、状态和路径边界。"""

from fastapi import HTTPException

from backend.app.api.assets import create_asset_router
from backend.app.documents.domain.models import DocumentRecord, DocumentStatus
from backend.app.documents.parsing.asset_registry import register_assets
from backend.tests.unit.support import MemoryDocumentStore


def test_asset_route_only_serves_registered_image_from_completed_document(tmp_path) -> None:
    """合法资源可读；未知、跨文档、未完成文档和路径穿越均返回 404。"""
    parsed_root = tmp_path / "parsed"
    image_dir = parsed_root / "doc-a" / "images"
    image_dir.mkdir(parents=True)
    image_bytes = b"test-image-bytes"
    (image_dir / "page_0_diagram.png").write_bytes(image_bytes)
    asset = register_assets("doc-a", parsed_root / "doc-a")[0]

    store = MemoryDocumentStore()
    store.save(DocumentRecord("doc-a", "a.pdf", DocumentStatus.COMPLETED))
    store.save(DocumentRecord("doc-b", "b.pdf", DocumentStatus.COMPLETED))
    store.save(DocumentRecord("doc-pending", "pending.pdf", DocumentStatus.PARSING))
    store.save(DocumentRecord("..", "traversal.pdf", DocumentStatus.COMPLETED))

    router = create_asset_router(parsed_root, store)
    endpoint = router.routes[0].endpoint

    response = endpoint("doc-a", asset.asset_id)
    assert response.body == image_bytes
    assert response.media_type == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"

    rejected_requests = [
        ("doc-a", "000000000000000000000000"),
        ("doc-b", asset.asset_id),
        ("doc-pending", asset.asset_id),
        ("..", asset.asset_id),
        ("../outside", asset.asset_id),
    ]
    for document_id, asset_id in rejected_requests:
        try:
            endpoint(document_id, asset_id)
        except HTTPException as error:
            assert error.status_code == 404
        else:
            raise AssertionError(f"未拒绝非法图片请求：{document_id}/{asset_id}")

