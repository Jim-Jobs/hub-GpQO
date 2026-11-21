from pydantic import BaseModel

from app.domain.enums import Sentiment


class PipelineResult(BaseModel):
    sentiment: Sentiment
    reply: str


if __name__ == "__main__":
    result = PipelineResult(sentiment=Sentiment.NEGATIVE, reply="先允许自己难过一会儿。")
    print(result.model_dump(mode="json"))
