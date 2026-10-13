import re
from typing import Annotated, Union
import requests
TOKEN = "7e379d7f3330"

from fastmcp import FastMCP
mcp = FastMCP(
    name="Tools-MCP-Server",
    instructions="""This server contains some api of tools.""",
)

@mcp.tool
def get_city_weather(city_name: Annotated[str, "The Pinyin of the city name (e.g., 'beijing' or 'shanghai')"]):
    """Retrieves the current weather data using the city's Pinyin name."""
    try:
        return requests.get(f"https://whyta.cn/api/tianqi?key={TOKEN}&city={city_name}").json()["data"]
    except:
        return []

@mcp.tool
def get_address_detail(address_text: Annotated[str, "City Name"]):
    """Parses a raw address string to extract detailed components (province, city, district, etc.)."""
    try:
        return requests.get(f"https://whyta.cn/api/tx/addressparse?key={TOKEN}&text={address_text}").json()["result"]
    except:
        return []

@mcp.tool
def get_tel_info(tel_no: Annotated[str, "Tel phone number"]):
    """Retrieves basic information (location, carrier) for a given telephone number."""
    try:
        return requests.get(f"https://whyta.cn/api/tx/mobilelocal?key={TOKEN}&phone={tel_no}").json()["result"]
    except:
        return []

@mcp.tool
def get_scenic_info(scenic_name: Annotated[str, "Scenic/tourist place name"]):
    """Searches for and retrieves information about a specific scenic spot or tourist attraction."""
    # https://apis.whyta.cn/docs/tx-scenic.html
    try:
        return requests.get(f"https://whyta.cn/api/tx/scenic?key={TOKEN}&word={scenic_name}").json()["result"]["list"]
    except:
        return []

@mcp.tool
def get_flower_info(flower_name: Annotated[str, "Flower name"]):
    """Retrieves the flower language (花语) and details for a given flower name."""
    # https://apis.whyta.cn/docs/tx-huayu.html
    try:
        return requests.get(f"https://whyta.cn/api/tx/huayu?key={TOKEN}&word={flower_name}").json()["result"]
    except:
        return []

@mcp.tool
def get_rate_transform(
    source_coin: Annotated[str, "The three-letter code (e.g., USD, CNY) for the source currency."], 
    aim_coin: Annotated[str, "The three-letter code (e.g., EUR, JPY) for the target currency."], 
    money: Annotated[Union[int, float], "The amount of money to convert."]
):
    """Calculates the currency exchange conversion amount between two specified coins."""
    try:
        return requests.get(f"https://whyta.cn/api/tx/fxrate?key={TOKEN}&fromcoin={source_coin}&tocoin={aim_coin}&money={money}").json()["result"]["money"]
    except:
        return []


@mcp.tool
def sentiment_classification(text: Annotated[str, "The text to analyze"]):
    """Classifies the sentiment of a given text."""
    positive_keywords_zh = ['喜欢', '赞', '棒', '优秀', '精彩', '完美', '开心', '满意']
    negative_keywords_zh = ['差', '烂', '坏', '糟糕', '失望', '垃圾', '厌恶', '敷衍']

    positive_pattern = '(' + '|'.join(positive_keywords_zh) + ')'
    negative_pattern = '(' + '|'.join(negative_keywords_zh) + ')'

    positive_matches = re.findall(positive_pattern, text)
    negative_matches = re.findall(negative_pattern, text)

    count_positive = len(positive_matches)
    count_negative = len(negative_matches)

    if count_positive > count_negative:
        return "积极 (Positive)"
    elif count_negative > count_positive:
        return "消极 (Negative)"
    else:
        return "中性 (Neutral)"


@mcp.tool
def query_salary_info(user_name: Annotated[str, "用户名"]):
    """Query user salary baed on the username."""

    if len(user_name) == 2:
        return 1000
    elif len(user_name) == 3:
        return 2000
    else:
        return 3000


@mcp.tool
def query_salary_info(user_name: Annotated[str, "用户名"]):
    """Query user salary baed on the username."""

    if len(user_name) == 2:
        return 1000
    elif len(user_name) == 3:
        return 2000
    else:
        return 3000


@mcp.tool
def work_survival_guide(overtime_hours: Annotated[int, "今日加班时长(小时)"],
                        boss_mood: Annotated[str, "老板心情(好/正常/暴躁)"]):
    """Get survival advice based on overtime and boss mood."""

    if overtime_hours == 0:
        return "准点下班的人，建议低头快走，别在老板面前晃悠"
    elif overtime_hours < 2:
        if boss_mood == "暴躁":
            return "加班不够，建议再熬半小时，别当出头鸟🐤"
        else:
            return "加得刚刚好，明天可以摸鱼2小时"
    else:
        return f"加班{overtime_hours}小时勇士，老板心情{boss_mood}时建议：\n- 立刻提交周报\n- 回复所有未读消息\n- 然后默默消失"


@mcp.tool
def daily_horoscope(zodiac: Annotated[str, "星座"],
                    mood: Annotated[str, "今天心情(好/一般/差)"]):
    """Predict daily fortune based on zodiac and mood."""

    if mood == "好":
        return f"{zodiac}座今天运气爆棚，买彩票可能中5块，表白成功率+10%"
    elif mood == "差":
        return f"{zodiac}座今天建议躺平，吵架可能吵不赢，外卖可能被偷"
    else:
        if zodiac in ["处女座", "摩羯座"]:
            return f"{zodiac}座心情一般也没关系，你本来就很佛系😌"
        else:
            return f"{zodiac}座建议立刻点杯奶茶，心情直升两个level"


@mcp.tool
def wechat_steps_detective(steps: Annotated[int, "微信步数"]):
    """Deduce what someone did today based on steps."""

    if steps < 100:
        return "今日状态：床就是我的一切，厕所是唯一的远行🚽"
    elif 100 <= steps < 1000:
        return "今日状态：去拿了次外卖+上了两次厕所，充实的一天呢"
    elif 1000 <= steps < 5000:
        return "今日状态：正常上班族，地铁站到公司的距离，毫无惊喜"
    elif 5000 <= steps < 10000:
        return "今日状态：逛了趟超市/商场，大概率是被女朋友拉去的"
    elif 10000 <= steps < 20000:
        return "今日状态：旅游/爬山/逛展，朋友圈素材已就绪📸"
    else:
        return "今日状态：马拉松选手？还是手机绑狗身上了？🐕"