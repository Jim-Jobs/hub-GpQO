"""从已完成文档摘要构建资料库主题概览。"""

from backend.app.documents.domain.models import CorpusProfile, DocumentRecord, DocumentStatus


def update_corpus_profile(records: list[DocumentRecord]) -> CorpusProfile:
    """按文档 ID 排序汇总已完成文档，忽略未完成或无摘要记录。"""
    completed_by_id = {
        record.document_id: record
        for record in records
        if record.status is DocumentStatus.COMPLETED and record.summary
    }
    completed = sorted(completed_by_id.values(), key=lambda record: record.document_id)
    summary = "\n".join(
        f"- {record.title or record.file_name}: {record.summary}" for record in completed
    )
    return CorpusProfile(document_count=len(completed), summary=summary)
