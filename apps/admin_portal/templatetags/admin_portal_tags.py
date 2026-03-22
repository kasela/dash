from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get a dictionary value by key in templates."""
    return dictionary.get(key)
