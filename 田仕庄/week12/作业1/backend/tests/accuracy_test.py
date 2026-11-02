"""
准确率验收脚本（端到端自动化测试）。

用法：
    # 直连模型服务模式（默认，需先配置 .env 中的 DEEPSEEK_API_KEY）
    python tests/accuracy_test.py

    # HTTP 模式（需先启动服务）
    python tests/accuracy_test.py --url http://127.0.0.1:8000

    # 指定测试集目录与准确率阈值
    python tests/accuracy_test.py --dir ../test_images --threshold 0.8

测试集目录结构（目录名即标注类别）：
    test_images/
      高风险/*.jpg
      中风险/*.jpg
      低风险/*.jpg
      无风险/*.jpg
      非楼道/*.jpg

退出码：准确率达到阈值返回 0，否则返回 1。全部过程写入 logs/accuracy.log。
"""
import argparse
import base64
import mimetypes
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from loguru import logger  # noqa: E402

from app.config import settings  # noqa: E402
from app.logger import setup_logging  # noqa: E402
from app.prompt import VALID_CATEGORIES  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def predict_direct(image_path: Path) -> str:
    """直连模式：进程内调用 VisionService。"""
    from app.services.vision import VisionService

    if not hasattr(predict_direct, "_service"):
        predict_direct._service = VisionService()
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        mime = "image/jpeg"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    result, _ = predict_direct._service.recognize(b64, mime)
    return result.category


def predict_http(image_path: Path, base_url: str) -> str:
    """HTTP 模式：调用运行中的服务接口。"""
    import httpx

    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/v1/recognize",
        json={"image_base64": b64, "mime_type": mime},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["category"]


def main() -> int:
    parser = argparse.ArgumentParser(description="楼道风险识别准确率验收")
    parser.add_argument("--dir", default=str(PROJECT_ROOT / "test_images"), help="标注测试集目录")
    parser.add_argument("--url", default=None, help="HTTP 模式的服务地址，缺省为直连模式")
    parser.add_argument("--threshold", type=float, default=0.8, help="准确率阈值（默认 0.8）")
    args = parser.parse_args()

    setup_logging()
    test_dir = Path(args.dir)
    acc_log = settings.log_dir / "accuracy.log"
    logger.add(acc_log, level="INFO", rotation="00:00", retention="90 days", encoding="utf-8")

    if not test_dir.exists():
        logger.error("测试集目录不存在: {}", test_dir)
        return 1

    # 收集样本
    samples = []
    for category in VALID_CATEGORIES:
        cat_dir = test_dir / category
        if not cat_dir.is_dir():
            continue
        for path in sorted(cat_dir.iterdir()):
            if path.suffix.lower() in IMAGE_EXTS:
                samples.append((path, category))

    if not samples:
        logger.error("测试集为空，请将标注图片按类别放入 {}", test_dir)
        return 1

    logger.info("=" * 60)
    logger.info("开始准确率验收 | 模式={} | 样本数={} | 阈值={:.0%}",
                "HTTP" if args.url else "直连", len(samples), args.threshold)
    logger.info("=" * 60)

    predict = (lambda p: predict_http(p, args.url)) if args.url else predict_direct

    confusion = {c: {c2: 0 for c2 in VALID_CATEGORIES} for c in VALID_CATEGORIES}
    correct = total = failed = 0

    for idx, (path, expected) in enumerate(samples, 1):
        try:
            predicted = predict(path)
            confusion[expected][predicted] += 1
            ok = predicted == expected
            correct += ok
            logger.info("[{}/{}] {} | 期望={} 预测={} {}",
                        idx, len(samples), path.name, expected, predicted, "✓" if ok else "✗")
        except Exception as exc:  # 单张失败不中断整批
            failed += 1
            logger.error("[{}/{}] {} | 期望={} 识别异常: {}", idx, len(samples), path.name, expected, exc)
        total += 1

    accuracy = correct / total if total else 0.0

    # 汇总报告
    logger.info("=" * 60)
    logger.info("验收结果: 总数={} 正确={} 异常={} 准确率={:.1%}（阈值 {:.0%}）",
                total, correct, failed, accuracy, args.threshold)
    header = "期望\\预测\t" + "\t".join(VALID_CATEGORIES)
    logger.info("混淆矩阵:\n{}", header)
    for expected in VALID_CATEGORIES:
        row = confusion[expected]
        if any(row.values()):
            line = expected + "\t" + "\t".join(str(row[c]) for c in VALID_CATEGORIES)
            logger.info(line)
    logger.info("=" * 60)

    passed = accuracy >= args.threshold
    logger.info("验收结论: {}", "通过 ✓" if passed else "未通过 ✗")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
