"""Parser package: one module per retailer, same shape each time.

Each parser exposes:
  parse_page_html(html, page_url, seen_at) -> (listings, has_next, tiles_seen)
  scrape_<retailer>(pw, ...) -> (listings, ParserHealth)   [async driver]
"""
