"""Whisky deal-hunt scraper: weekly automated price hunting.

Replaces the browser-based weekly check with a scheduled job that covers
all tracked Singapore retailers fully, compares each listing against
verified in-stock overseas retail prices, and writes a JSON report for
Muse to verify and report on. The scraper never purchases, never adds to
a cart, and never tests a promo code in a basket.
"""
