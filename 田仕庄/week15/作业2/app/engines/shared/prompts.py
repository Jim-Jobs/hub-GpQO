from app.domain.enums import Sentiment

_SENTIMENT_TEMPLATE = """你是一个严格的情感二分类器。
请只判断用户最后一条消息的整体情感，只输出一个英文单词：positive 或 negative。
不要输出解释、标点、换行或任何其他内容。

用户消息：{message}
"""

_POSITIVE_REPLY_TEMPLATE = """你是一位温和、真诚的陪伴者。用户刚刚分享了积极的消息：
「{message}」

请用简短、自然的中文回应，肯定用户的感受，分享一点喜悦，不要夸张，不要给建议清单。
控制在 80 字以内，只输出回复正文。
"""

_NEGATIVE_REPLY_TEMPLATE = """你是一位温和、克制的陪伴者。用户刚刚表达了低落或负面情绪：
「{message}」

请先用一句话共情，再给出一句温和的排解或鼓励，不要说教，不要提供医疗诊断，
不要承诺事情一定会变好。控制在 80 字以内，只输出回复正文。
"""


def build_sentiment_prompt(message: str) -> str:
    return _SENTIMENT_TEMPLATE.format(message=message)


def build_reply_prompt(sentiment: Sentiment, message: str) -> str:
    template = (
        _POSITIVE_REPLY_TEMPLATE
        if sentiment is Sentiment.POSITIVE
        else _NEGATIVE_REPLY_TEMPLATE
    )
    return template.format(message=message)


if __name__ == "__main__":
    print(build_sentiment_prompt("今天面试没过"))
    print(build_reply_prompt(Sentiment.NEGATIVE, "今天面试没过"))
