import json

from django import template

register = template.Library()


@register.filter
def get_item(data: dict, key: str):
    return data.get(key)


@register.filter
def to_json(value) -> str:
    return json.dumps(value)
