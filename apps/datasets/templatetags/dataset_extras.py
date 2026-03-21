from django import template

register = template.Library()


@register.filter
def get_item(data: dict, key: str):
    return data.get(key)
