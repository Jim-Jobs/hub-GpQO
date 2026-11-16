"""网页上传 PDF、查看解析状态，并把图文问题交给 Qwen3.5。"""

import cgi
import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from common import (
    connect_redis,
    ensure_topic,
    list_docs,
    load_done_chunks,
    make_producer,
    new_doc,
    publish_parse_job,
    save_doc,
    set_status,
)
from indexlib import retrieve

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("RAG_DATA", ROOT / "data")).resolve()
MODEL_PATH = os.environ.get("MODEL_PATH", "/models/Qwen3.5-0.8B")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
INDEX = ROOT / "index.html"

REDIS = connect_redis()
PRODUCER = make_producer()
ENGINE_LOCK = threading.Lock()
ENGINE = {"model": None, "processor": None}


def shrink(image, limit: int = 1280):
    image = image.convert("RGB")
    width, height = image.size
    scale = limit / max(width, height)
    if scale < 1:
        image = image.resize((max(int(width * scale), 1), max(int(height * scale), 1)))
    return image


def load_engine():
    if ENGINE["model"] is not None:
        return ENGINE["model"], ENGINE["processor"]
    import torch
    import transformers
    from transformers import AutoProcessor

    model_class = None
    for name in ("Qwen3_5ForConditionalGeneration", "AutoModelForImageTextToText", "AutoModelForVision2Seq"):
        model_class = getattr(transformers, name, None)
        if model_class is not None:
            break
    if model_class is None:
        raise RuntimeError("当前 transformers 不能加载 Qwen3.5，需要 5.2 或更新版本")

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    print(f"加载模型 {MODEL_PATH}", flush=True)
    kwargs = {
        "local_files_only": True,
        "trust_remote_code": True,
    }
    try:
        model = model_class.from_pretrained(MODEL_PATH, device_map="auto", torch_dtype=dtype, **kwargs)
    except TypeError:
        try:
            model = model_class.from_pretrained(MODEL_PATH, device_map="auto", dtype=dtype, **kwargs)
        except Exception:
            model = model_class.from_pretrained(MODEL_PATH, dtype=dtype, **kwargs)
            if torch.cuda.is_available():
                model = model.to("cuda")
    except Exception:
        model = model_class.from_pretrained(MODEL_PATH, torch_dtype=dtype, **kwargs)
        if torch.cuda.is_available():
            model = model.to("cuda")
    model.eval()
    processor = AutoProcessor.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=True)
    ENGINE["model"] = model
    ENGINE["processor"] = processor
    return model, processor


def build_prompt(question: str, hits: list[dict], has_user_image: bool) -> str:
    lines = [
        "你是文档问答助手。只根据下面的资料和图片回答，资料里没有的内容要直接说明。",
        "文档图片按给出的顺序从 0 编号。需要展示某一张时，在对应句子后面写 {{img:编号}}。",
    ]
    if has_user_image:
        lines.append("消息里的最后一张图片是用户随问题上传的，请看图回答，不要用 {{img}} 引用它。")
    if hits:
        lines.append("资料：")
        for index, hit in enumerate(hits):
            mark = f"图片{index}" if hit.get("images") else "无单独配图"
            lines.append(f"[{hit['filename']} 第{hit['page']}页，{mark}]\n{hit['text']}")
    else:
        lines.append("当前没有检索到已解析文档。")
    lines.append(f"问题：{question}")
    return "\n\n".join(lines)


def generate_answer(question: str, image_paths: list[Path], hits: list[dict], has_user_image: bool) -> str:
    from PIL import Image
    import torch

    with ENGINE_LOCK:
        model, processor = load_engine()
        content = []
        opened = []
        for path in image_paths:
            image = shrink(Image.open(path))
            opened.append(image)
            content.append({"type": "image", "image": image})
        content.append({"type": "text", "text": build_prompt(question, hits, has_user_image)})
        inputs = processor.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        if hasattr(inputs, "to"):
            inputs = inputs.to(model.device)
        else:
            inputs = {key: value.to(model.device) if hasattr(value, "to") else value for key, value in inputs.items()}
        input_ids = inputs["input_ids"] if isinstance(inputs, dict) else inputs.input_ids
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=512, do_sample=False)
        trimmed = [out[len(src) :] for src, out in zip(input_ids, generated)]
        text = processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
        for image in opened:
            image.close()
    import re

    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def safe_name(filename: str) -> str:
    name = Path(filename or "document.pdf").name.replace("\x00", "")
    return name or "document.pdf"


def field_list(form, name: str):
    if name not in form:
        return []
    item = form[name]
    if isinstance(item, list):
        return item
    return [item]


class Handler(BaseHTTPRequestHandler):
    server_version = "mmrag/1.0"

    def log_message(self, fmt, *args):
        print(f"[web] {self.address_string()} {fmt % args}", flush=True)

    def send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code: int, payload) -> None:
        self.send_bytes(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def read_form(self):
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )

    def do_GET(self):
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self.send_bytes(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/docs":
            self.send_json(200, {"docs": list_docs(REDIS)})
            return
        if path.startswith("/media/"):
            self.serve_media(path[len("/media/") :])
            return
        self.send_json(404, {"error": "没有这个地址"})

    def serve_media(self, rel: str) -> None:
        target = (DATA_DIR / unquote(rel)).resolve()
        if not target.is_file() or DATA_DIR not in target.parents:
            self.send_json(404, {"error": "文件不存在"})
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_bytes(200, target.read_bytes(), mime)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                self.upload()
            elif path == "/api/ask":
                self.ask()
            else:
                self.send_json(404, {"error": "没有这个地址"})
        except Exception as exc:
            print(f"[web] 请求失败: {exc}", flush=True)
            self.send_json(500, {"error": str(exc)})

    def upload(self) -> None:
        form = self.read_form()
        created = []
        upload_dir = DATA_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        for item in field_list(form, "files"):
            filename = safe_name(getattr(item, "filename", ""))
            if not filename.lower().endswith(".pdf"):
                continue
            payload = item.file.read()
            if not payload:
                continue
            doc = new_doc(filename)
            dest = upload_dir / f"{doc['id']}.pdf"
            dest.write_bytes(payload)
            save_doc(REDIS, doc)
            try:
                publish_parse_job(PRODUCER, doc["id"], filename, str(dest))
            except Exception as exc:
                set_status(REDIS, doc["id"], "failed", f"任务没有进入 Kafka: {exc}")
            created.append({"id": doc["id"], "filename": filename, "status_text": "待解析"})
        if not created:
            self.send_json(400, {"error": "请上传至少一个 PDF"})
            return
        self.send_json(200, {"docs": created})

    def ask(self) -> None:
        form = self.read_form()
        raw_question = form.getvalue("question")
        if isinstance(raw_question, list):
            raw_question = raw_question[0] if raw_question else ""
        if isinstance(raw_question, bytes):
            raw_question = raw_question.decode("utf-8", "ignore")
        question = str(raw_question or "").strip()
        image_item = form["image"] if "image" in form else None
        user_image = None
        if image_item is not None and getattr(image_item, "filename", ""):
            suffix = Path(safe_name(image_item.filename)).suffix.lower() or ".png"
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                self.send_json(400, {"error": "提问图片只支持常见图片格式"})
                return
            folder = DATA_DIR / "questions"
            folder.mkdir(parents=True, exist_ok=True)
            user_image = folder / f"{os.urandom(6).hex()}{suffix}"
            user_image.write_bytes(image_item.file.read())
        if not question and user_image is None:
            self.send_json(400, {"error": "请输入问题或上传图片"})
            return
        if not question:
            question = "请结合图片回答。"
        if not Path(MODEL_PATH).exists():
            self.send_json(500, {"error": f"找不到模型目录 {MODEL_PATH}"})
            return

        hits = retrieve(load_done_chunks(REDIS), question, limit=4)
        shown = []
        model_images = []
        for hit in hits:
            for rel in hit.get("images") or []:
                path = DATA_DIR / rel
                if not path.is_file():
                    continue
                shown.append(
                    {
                        "url": "/media/" + rel,
                        "caption": f"{hit['filename']} 第{hit['page']}页",
                    }
                )
                model_images.append(path)
                if len(shown) >= 3:
                    break
            if len(shown) >= 3:
                break
        has_user_image = user_image is not None
        if has_user_image:
            model_images.append(user_image)
        answer = generate_answer(question, model_images, hits, has_user_image)
        self.send_json(
            200,
            {
                "answer": answer,
                "images": shown,
                "user_image": f"/media/{user_image.relative_to(DATA_DIR)}" if user_image else "",
                "sources": [
                    {"filename": hit["filename"], "page": hit["page"]}
                    for hit in hits
                ],
            },
        )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_topic()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"网页已启动 http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
