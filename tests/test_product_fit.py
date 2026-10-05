import pytest

from geo_blog.product_fit import choose_product


def test_rotation_prefers_relevant_product_not_recently_used():
    a = {
        "name": "A",
        "url": "https://geo-insulation.com/agents/a/",
        "reason": "Relevant",
    }
    b = {
        "name": "B",
        "url": "https://geo-insulation.com/agents/b/",
        "reason": "Relevant",
    }
    assert choose_product([a, b], {a["url"], b["url"]}, [a["url"]]) == b
    assert choose_product([a], {a["url"]}, [a["url"]]) == a


def test_unverified_product_is_not_selected_for_variety():
    with pytest.raises(ValueError):
        choose_product([{"name": "Fake", "url": "https://fake.test/", "reason": "Invented"}], set())
