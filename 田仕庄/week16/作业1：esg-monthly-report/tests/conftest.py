"""共享 fixture：tests 内所有用例的样本数据。"""

from datetime import date

import pytest


def sample_profile_data() -> dict:
    return {
        "client": "shenhua",
        "industry": "煤炭开采与煤电一体化",
        "keywords": ["储能", "煤电", "CCUS"],
        "whitelist": ["nea.gov.cn", "ndrc.gov.cn", "shenhua.com"],
    }


def sample_items_data() -> list[dict]:
    return [
        {
            "source": "国家能源局",
            "url": "https://www.nea.gov.cn/a",
            "date": "2026-10-20",
            "title": "新型储能发展指导意见发布",
            "impact_level": "高",
            "impact_one_liner": "储能政策利好煤电灵活性改造配套业务。",
            "section": "policy",
        },
        {
            "source": "中国神华官网",
            "url": "https://www.shenhua.com/b",
            "date": "2026-10-12",
            "title": "神华发布三季度经营数据",
            "impact_level": "中",
            "impact_one_liner": "经营数据平稳，碳排放强度持平。",
            "section": "company",
        },
    ]


@pytest.fixture
def now():
    return date(2026, 11, 3)


@pytest.fixture
def sample_profile():
    return sample_profile_data()


@pytest.fixture
def sample_items():
    return sample_items_data()
