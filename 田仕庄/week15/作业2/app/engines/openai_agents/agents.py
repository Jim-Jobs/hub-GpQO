from agents import Agent, OpenAIChatCompletionsModel

from app.domain.enums import Sentiment
from app.engines.shared.prompts import build_reply_prompt, build_sentiment_prompt


def build_sentiment_agent(model: OpenAIChatCompletionsModel, message: str) -> Agent:
    return Agent(
        name="SentimentAgent",
        model=model,
        instructions=build_sentiment_prompt(message),
    )


def build_reply_agent(
    model: OpenAIChatCompletionsModel, sentiment: Sentiment, message: str
) -> Agent:
    return Agent(
        name="ReplyAgent",
        model=model,
        instructions=build_reply_prompt(sentiment, message),
    )


if __name__ == "__main__":
    print("agents: SentimentAgent -> ReplyAgent")
