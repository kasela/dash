from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = "weekly"
    protocol = "https"

    def items(self):
        return ["landing", "pricing", "about", "blog", "contact", "privacy", "terms", "security", "signup", "login"]

    def location(self, item):
        return reverse(item)
