from enum import Enum


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


if __name__ == "__main__":
    print([(item.name, item.value) for item in Sentiment])
    print(Sentiment("negative") is Sentiment.NEGATIVE)
