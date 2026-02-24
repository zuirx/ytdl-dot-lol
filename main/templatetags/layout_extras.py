from django import template
from django.template.loader import get_template, TemplateDoesNotExist

register = template.Library()

@register.simple_tag(takes_context=True)
def safe_include(context, template_name):
    try:
        return get_template(template_name).render(context.flatten())
    except TemplateDoesNotExist:
        return ""