import { initializeChat } from "./chat/chat.mjs";
import { initializeDocuments } from "./documents/documents.mjs";

/** 按页面 DOM 装配文档上传与问答两个独立交互。 */
function main() {
  initializeDocuments({
    form: document.querySelector("#pdf-form"),
    fileInput: document.querySelector("#pdf-input"),
    list: document.querySelector("#pdf-list"),
    message: document.querySelector("#pdf-message"),
  });
  initializeChat({
    form: document.querySelector("#chat-form"),
    questionInput: document.querySelector("#question-input"),
    imageInput: document.querySelector("#question-image"),
    imagePreview: document.querySelector("#image-preview"),
    log: document.querySelector("#chat-log"),
  });
}

main();
